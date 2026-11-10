"""Batch calls. The dialing itself is done by services/scheduler.py (database-driven, restart-safe)."""
import json
import re
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db
from ..security import current_user, member
from ..services.templating import clean_vars
from ..services.versions import jload

router = APIRouter(prefix="/api/campaigns", tags=["batch calls"])
HM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Contact(BaseModel):
    phone: str
    name: Optional[str] = None
    variables: Optional[dict[str, Any]] = None


class CampaignIn(BaseModel):
    name: str
    agent_id: str
    phone_number_id: str
    concurrency: int = 1
    contacts: List[Contact]
    window_start: Optional[str] = None      # "09:30"
    window_end: Optional[str] = None        # "18:00"
    window_days: Optional[str] = "0,1,2,3,4,5"   # 0 = Monday
    timezone: Optional[str] = None
    max_attempts: int = 2
    retry_delay_min: int = 60
    start_now: bool = False


@router.get("")
def list_campaigns(u=Depends(current_user)):
    return db.query("""SELECT c.*, a.name AS agent_name, p.number AS from_number,
                       (SELECT COUNT(*) FROM campaign_contacts x WHERE x.campaign_id = c.id AND x.status = 'calling') AS calling,
                       (SELECT COUNT(*) FROM campaign_contacts x WHERE x.campaign_id = c.id AND x.status = 'retry') AS retrying
                       FROM campaigns c
                       LEFT JOIN agents a ON a.id = c.agent_id LEFT JOIN phone_numbers p ON p.id = c.phone_number_id
                       WHERE c.tenant_id = ? ORDER BY c.created_at DESC""", u["tenant_id"])


@router.post("")
def create(b: CampaignIn, u=Depends(member)):
    if not b.contacts:
        raise HTTPException(400, "Add at least one contact")
    if not db.one("SELECT id FROM agents WHERE id = ? AND tenant_id = ?", b.agent_id, u["tenant_id"]):
        raise HTTPException(404, "Agent not found")
    if not db.one("SELECT id FROM phone_numbers WHERE id = ? AND tenant_id = ?", b.phone_number_id, u["tenant_id"]):
        raise HTTPException(404, "Number not found")
    for v in (b.window_start, b.window_end):
        if v and not HM.match(v):
            raise HTTPException(400, "Calling window times must look like 09:30")
    contacts, seen = [], set()
    for ct in b.contacts[:10000]:
        phone = "+" + re.sub(r"\D", "", ct.phone)
        if len(phone) < 8 or phone in seen:
            continue
        seen.add(phone)
        contacts.append((phone, (ct.name or "")[:200] or None, clean_vars(ct.variables)))
    if not contacts:
        raise HTTPException(400, "No valid phone numbers")
    c = db.insert("campaigns", {"tenant_id": u["tenant_id"], "name": b.name, "agent_id": b.agent_id,
                                "phone_number_id": b.phone_number_id, "status": "running" if b.start_now else "draft",
                                "concurrency": max(1, min(b.concurrency, 20)), "total": len(contacts),
                                "completed": 0, "failed": 0, "created_at": db.now(),
                                "started_at": db.now() if b.start_now else None,
                                "window_start": b.window_start or None, "window_end": b.window_end or None,
                                "window_days": b.window_days or None, "timezone": b.timezone or None,
                                "max_attempts": max(1, min(b.max_attempts, 5)),
                                "retry_delay_min": max(5, min(b.retry_delay_min, 1440))})
    db.insert_many("campaign_contacts", [{"campaign_id": c["id"], "phone": phone, "name": name, "status": "pending",
                                          "attempts": 0, "variables_json": json.dumps(variables)}
                                         for phone, name, variables in contacts])
    db.audit(u["tenant_id"], u["id"], "campaign.create", b.name)
    return c


def _get(cid, u):
    c = db.one("SELECT * FROM campaigns WHERE id = ? AND tenant_id = ?", cid, u["tenant_id"])
    if not c:
        raise HTTPException(404)
    return c


@router.get("/{cid}")
def detail(cid: str, u=Depends(current_user)):
    c = _get(cid, u)
    rows = db.query("""SELECT cc.*, cl.duration_sec, cl.outcome, cl.ended_reason, cl.in_voicemail, cl.call_successful
                       FROM campaign_contacts cc LEFT JOIN calls cl ON cl.id = cc.call_id
                       WHERE cc.campaign_id = ? ORDER BY cc.phone""", cid)
    for r in rows:
        r["variables"] = jload(r.pop("variables_json", None), {})
    c["contacts"] = rows
    return c


@router.post("/{cid}/start")
def start(cid: str, u=Depends(member)):
    c = _get(cid, u)
    if c["status"] in ("completed", "stopped"):
        raise HTTPException(400, f"This batch is {c['status']}")
    db.update("campaigns", cid, {"status": "running", "started_at": c.get("started_at") or db.now()})
    return {"status": "running"}


@router.post("/{cid}/pause")
def pause(cid: str, u=Depends(member)):
    _get(cid, u)
    db.update("campaigns", cid, {"status": "paused"})        # calls in progress finish normally
    return {"status": "paused"}


@router.post("/{cid}/stop")
def stop(cid: str, u=Depends(member)):
    _get(cid, u)
    db.execute("UPDATE campaign_contacts SET status = 'skipped' WHERE campaign_id = ? AND status IN ('pending', 'retry')",
               cid)
    db.update("campaigns", cid, {"status": "stopped", "finished_at": db.now()})
    return {"status": "stopped"}


@router.post("/{cid}/retry-failed")
def retry_failed(cid: str, u=Depends(member)):
    c = _get(cid, u)
    n = db.execute("UPDATE campaign_contacts SET status = 'pending', next_attempt_at = NULL, attempts = 0 "
                   "WHERE campaign_id = ? AND status = 'failed'", cid)
    if n and c["status"] in ("completed", "stopped"):
        db.update("campaigns", cid, {"status": "paused"})
    return {"requeued": n}


@router.delete("/{cid}")
def delete(cid: str, u=Depends(member)):
    _get(cid, u)
    db.execute("DELETE FROM campaign_contacts WHERE campaign_id = ?", cid)
    db.execute("DELETE FROM campaigns WHERE id = ? AND tenant_id = ?", cid, u["tenant_id"])
    return {"ok": True}
