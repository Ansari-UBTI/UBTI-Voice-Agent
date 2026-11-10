import asyncio
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db
from ..security import admin, current_user, hash_password, sha
from ..services import netguard, postcall

router = APIRouter(prefix="/api", tags=["developer & settings"])
EVENTS = ("call.started", "call.ended", "call.analyzed")


class KeyIn(BaseModel):
    name: str


class HookIn(BaseModel):
    url: str
    events: str = "call.ended,call.analyzed"


class OrgIn(BaseModel):
    name: Optional[str] = None
    country: Optional[str] = None
    timezone: Optional[str] = None


class PrivacyIn(BaseModel):
    pii_redaction: Optional[bool] = None
    retention_days: Optional[int] = None      # 0 = keep forever


class MemberIn(BaseModel):
    name: str
    email: str
    password: str
    role: str = "member"


class RoleIn(BaseModel):
    role: str


class ProfileIn(BaseModel):
    name: Optional[str] = None
    password: Optional[str] = None


@router.get("/keys")
def keys(u=Depends(admin)):
    return db.query("SELECT id, name, key_prefix, created_at, last_used_at FROM api_keys "
                    "WHERE tenant_id = ? AND revoked = 0 ORDER BY created_at DESC", u["tenant_id"])


@router.post("/keys")
def create_key(b: KeyIn, u=Depends(admin)):
    raw = "ubti_" + secrets.token_urlsafe(32)
    db.insert("api_keys", {"tenant_id": u["tenant_id"], "name": b.name, "key_prefix": raw[:12],
                           "key_hash": sha(raw), "revoked": False, "created_at": db.now()})
    db.audit(u["tenant_id"], u["id"], "apikey.create", b.name)
    return {"key": raw}


@router.delete("/keys/{kid}")
def revoke_key(kid: str, u=Depends(admin)):
    db.execute("UPDATE api_keys SET revoked = 1 WHERE id = ? AND tenant_id = ?", kid, u["tenant_id"])
    return {"ok": True}


@router.get("/webhooks")
def hooks(u=Depends(admin)):
    return db.query("SELECT * FROM webhooks WHERE tenant_id = ? ORDER BY created_at DESC", u["tenant_id"])


def _events(s: str) -> str:
    ev = [e.strip() for e in (s or "").split(",") if e.strip() in EVENTS or e.strip() == "*"]
    if not ev:
        raise HTTPException(400, f"Choose at least one event: {', '.join(EVENTS)}")
    return ",".join(ev)


@router.post("/webhooks")
async def add_hook(b: HookIn, u=Depends(admin)):
    try:
        await netguard.check(b.url)
    except netguard.BlockedURL as e:
        raise HTTPException(400, str(e))
    return await asyncio.to_thread(db.insert, "webhooks", {
        "tenant_id": u["tenant_id"], "url": b.url, "events": _events(b.events),
        "secret": "whsec_" + secrets.token_hex(16), "active": True, "created_at": db.now()})


@router.put("/webhooks/{hid}")
def update_hook(hid: str, b: HookIn, u=Depends(admin)):
    db.update("webhooks", hid, {"url": b.url, "events": _events(b.events)}, u["tenant_id"])
    return {"ok": True}


@router.post("/webhooks/{hid}/test")
async def test_hook(hid: str, u=Depends(admin)):
    h = await asyncio.to_thread(db.one, "SELECT * FROM webhooks WHERE id = ? AND tenant_id = ?", hid, u["tenant_id"])
    if not h:
        raise HTTPException(404)
    status = await postcall.deliver(h, "test", {"message": "Hello from UBTI Voice Agent"})
    if status is None:
        raise HTTPException(400, "Endpoint not reachable")
    return {"status": status}


@router.delete("/webhooks/{hid}")
def del_hook(hid: str, u=Depends(admin)):
    db.execute("DELETE FROM webhooks WHERE id = ? AND tenant_id = ?", hid, u["tenant_id"])
    return {"ok": True}


@router.get("/settings/org")
def org(u=Depends(current_user)):
    t = db.one("SELECT * FROM tenants WHERE id = ?", u["tenant_id"])
    t.pop("webhook_key", None)
    return t


@router.put("/settings/org")
def update_org(b: OrgIn, u=Depends(admin)):
    if b.timezone:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(b.timezone)
        except Exception:
            raise HTTPException(400, "Unknown timezone - use a name like Asia/Kolkata or Asia/Dubai")
    db.update("tenants", u["tenant_id"], {k: v for k, v in b.model_dump().items() if v is not None})
    return org(u)


@router.put("/settings/privacy")
def update_privacy(b: PrivacyIn, u=Depends(admin)):
    data = {}
    if b.pii_redaction is not None:
        data["pii_redaction"] = b.pii_redaction
    if b.retention_days is not None:
        data["retention_days"] = max(0, min(b.retention_days, 3650))
    db.update("tenants", u["tenant_id"], data)
    db.audit(u["tenant_id"], u["id"], "privacy.update", str(data))
    return org(u)


@router.get("/settings/team")
def team(u=Depends(current_user)):
    return db.query("SELECT id, name, email, role, last_login_at, created_at FROM users WHERE tenant_id = ? "
                    "AND is_active = 1 ORDER BY created_at", u["tenant_id"])


@router.post("/settings/team")
def add_member(b: MemberIn, u=Depends(admin)):
    email = b.email.strip().lower()
    if "@" not in email or len(b.password) < 8:
        raise HTTPException(400, "Valid email and a password of 8+ characters needed")
    if db.one("SELECT id FROM users WHERE email = ?", email):
        raise HTTPException(400, "Email already exists")
    db.insert("users", {"tenant_id": u["tenant_id"], "email": email, "name": b.name,
                        "role": b.role if b.role in ("admin", "member", "viewer") else "member",
                        "password_hash": hash_password(b.password), "is_superadmin": False,
                        "is_active": True, "created_at": db.now()})
    db.audit(u["tenant_id"], u["id"], "team.add", email)
    return {"ok": True}


@router.put("/settings/team/{uid}")
def change_role(uid: str, b: RoleIn, u=Depends(admin)):
    if b.role not in ("admin", "member", "viewer") or uid == u["id"]:
        raise HTTPException(400, "Invalid role change")
    db.execute("UPDATE users SET role = ? WHERE id = ? AND tenant_id = ? AND role <> 'owner'", b.role, uid,
               u["tenant_id"])
    db.audit(u["tenant_id"], u["id"], "team.role", f"{uid} -> {b.role}")
    return {"ok": True}


@router.delete("/settings/team/{uid}")
def remove_member(uid: str, u=Depends(admin)):
    if uid == u["id"]:
        raise HTTPException(403)
    db.execute("UPDATE users SET is_active = 0 WHERE id = ? AND tenant_id = ? AND role <> 'owner'", uid, u["tenant_id"])
    return {"ok": True}


@router.put("/settings/profile")
def profile(b: ProfileIn, u=Depends(current_user)):
    data = {}
    if b.name:
        data["name"] = b.name
    if b.password:
        if len(b.password) < 8:
            raise HTTPException(400, "Password must be at least 8 characters")
        data["password_hash"] = hash_password(b.password)
    db.update("users", u["id"], data)
    return {"ok": True}


@router.get("/settings/audit")
def audit(u=Depends(admin)):
    return db.query("""SELECT TOP 200 l.action, l.detail, l.created_at, us.name AS user_name FROM audit_logs l
                       LEFT JOIN users us ON us.id = l.user_id WHERE l.tenant_id = ? ORDER BY l.created_at DESC""",
                    u["tenant_id"])
