import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db
from ..config import settings
from ..security import admin, current_user, encrypt, member, platform_key, tenant_key
from ..services import hot

router = APIRouter(prefix="/api/numbers", tags=["numbers"])
PROVIDERS = ("vobiz", "twilio", "acs")


class NumberIn(BaseModel):
    provider: str = "vobiz"
    number: str
    label: Optional[str] = None
    agent_id: Optional[str] = None
    country: Optional[str] = "IN"


class NumberUpd(BaseModel):
    label: Optional[str] = None
    agent_id: Optional[str] = None
    status: Optional[str] = None


class CredIn(BaseModel):
    provider: str
    account_id: str = ""
    auth_token: str          # ACS: the resource connection string


def _webhooks(provider: str, tenant_id: str, own_acs: bool = True) -> dict:
    b = settings.PUBLIC_BASE_URL
    if provider == "acs":
        k = tenant_key(tenant_id) if own_acs else platform_key()
        return {"event_grid_url": f"{b}/telephony/acs/incoming?k={k}",
                "event_type": "Microsoft.Communication.IncomingCall", "method": "POST"}
    k = tenant_key(tenant_id)
    return {"answer_url": f"{b}/telephony/{provider}/answer?k={k}",
            "status_url": f"{b}/telephony/{provider}/status?k={k}", "method": "POST"}


def _own_acs(tenant_id: str) -> bool:
    return bool(db.one("SELECT id FROM provider_credentials WHERE tenant_id = ? AND provider = 'acs'", tenant_id))


@router.get("/providers")
def providers(u=Depends(current_user)):
    rows = db.query("SELECT provider, account_id, updated_at FROM provider_credentials WHERE tenant_id = ?",
                    u["tenant_id"])
    have = {r["provider"]: r for r in rows}
    env = {"vobiz": bool(settings.VOBIZ_AUTH_ID), "twilio": bool(settings.TWILIO_ACCOUNT_SID),
           "acs": bool(settings.ACS_CONNECTION_STRING)}
    out = []
    for p in PROVIDERS:
        r = have.get(p)
        wh = _webhooks(p, u["tenant_id"], own_acs=bool(r)) if u.get("role") in ("owner", "admin") or \
            u.get("is_superadmin") else {}
        out.append({"provider": p, "connected": bool(r), "account_id": (r or {}).get("account_id", ""),
                    "platform_default": env[p], "webhooks": wh})
    return out


@router.put("/providers")
def save_provider(b: CredIn, u=Depends(admin)):
    if b.provider not in PROVIDERS:
        raise HTTPException(400, "Unknown provider")
    if b.provider == "acs" and "accesskey=" not in b.auth_token.lower():
        raise HTTPException(400, "Paste the ACS connection string (endpoint=https://...;accesskey=...)")
    account = b.account_id.strip()
    if b.provider == "acs":
        m = re.search(r"endpoint=https://([^/;]+)", b.auth_token, re.I)
        account = account or (m.group(1) if m else "acs")
    db.execute("DELETE FROM provider_credentials WHERE tenant_id = ? AND provider = ?", u["tenant_id"], b.provider)
    db.insert("provider_credentials", {"tenant_id": u["tenant_id"], "provider": b.provider, "account_id": account,
                                       "auth_token_enc": encrypt(b.auth_token.strip()), "updated_at": db.now()})
    hot.drop(("cred", u["tenant_id"], b.provider))
    db.audit(u["tenant_id"], u["id"], "provider.save", b.provider)
    return {"ok": True}


@router.delete("/providers/{provider}")
def remove_provider(provider: str, u=Depends(admin)):
    db.execute("DELETE FROM provider_credentials WHERE tenant_id = ? AND provider = ?", u["tenant_id"], provider)
    hot.drop(("cred", u["tenant_id"], provider))
    return {"ok": True}


@router.get("")
def list_numbers(u=Depends(current_user)):
    rows = db.query("""SELECT p.*, a.name AS agent_name FROM phone_numbers p LEFT JOIN agents a ON a.id = p.agent_id
                       WHERE p.tenant_id = ? ORDER BY p.created_at DESC""", u["tenant_id"])
    show = u.get("role") in ("owner", "admin") or u.get("is_superadmin")
    own_acs = _own_acs(u["tenant_id"]) if show else True
    for r in rows:
        r["webhooks"] = _webhooks(r["provider"], u["tenant_id"], own_acs) if show else {}
    return rows


def _check_agent(u, agent_id):
    if agent_id and not db.one("SELECT id FROM agents WHERE id = ? AND tenant_id = ?", agent_id, u["tenant_id"]):
        raise HTTPException(404, "Agent not found")


@router.post("")
def add_number(b: NumberIn, u=Depends(admin)):
    num = "+" + re.sub(r"\D", "", b.number)
    if len(num) < 8:
        raise HTTPException(400, "Enter the number in international format, e.g. +9180XXXXXXXX")
    if b.provider not in PROVIDERS:
        raise HTTPException(400, "Provider must be vobiz, twilio or acs")
    if db.one("SELECT id FROM phone_numbers WHERE number = ?", num):
        raise HTTPException(400, "This number is already connected")
    _check_agent(u, b.agent_id)
    r = db.insert("phone_numbers", {"tenant_id": u["tenant_id"], "provider": b.provider, "number": num,
                                    "label": b.label or num, "agent_id": b.agent_id or None,
                                    "country": b.country, "status": "active", "created_at": db.now()})
    hot.numbers_changed()
    db.audit(u["tenant_id"], u["id"], "number.add", num)
    return {**r, "webhooks": _webhooks(b.provider, u["tenant_id"], _own_acs(u["tenant_id"]))}


@router.put("/{nid}")
def update_number(nid: str, b: NumberUpd, u=Depends(member)):
    data = {k: v for k, v in b.model_dump().items() if v is not None}
    if "agent_id" in b.model_fields_set and not b.agent_id:
        data["agent_id"] = None
    _check_agent(u, data.get("agent_id"))
    if data.get("status") and data["status"] not in ("active", "inactive"):
        raise HTTPException(400, "status must be active or inactive")
    db.update("phone_numbers", nid, data, u["tenant_id"])
    hot.numbers_changed()
    return {"ok": True}


@router.delete("/{nid}")
def delete_number(nid: str, u=Depends(admin)):
    db.execute("DELETE FROM phone_numbers WHERE id = ? AND tenant_id = ?", nid, u["tenant_id"])
    hot.numbers_changed()
    db.audit(u["tenant_id"], u["id"], "number.delete", nid)
    return {"ok": True}
