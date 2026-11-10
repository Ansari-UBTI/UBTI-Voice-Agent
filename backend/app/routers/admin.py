from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db
from ..config import settings
from ..security import require_admin
from ..services import hot, speech
from ..services.aio import spawn
from .analytics import overview_for
from .auth import PLAN_QUOTA

router = APIRouter(prefix="/api/admin", tags=["admin"])


class TenantUpd(BaseModel):
    status: Optional[str] = None
    plan: Optional[str] = None
    minutes_quota: Optional[int] = None
    max_concurrent_calls: Optional[int] = None


class Review(BaseModel):
    approve: bool
    note: Optional[str] = None


@router.get("/overview")
def overview(days: int = 30, a=Depends(require_admin)):
    data = overview_for(None, days)
    data["platform"] = db.one("""SELECT (SELECT COUNT(*) FROM tenants) AS tenants,
        (SELECT COUNT(*) FROM tenants WHERE status = 'active') AS active_tenants,
        (SELECT COUNT(*) FROM users WHERE is_active = 1) AS users, (SELECT COUNT(*) FROM agents) AS agents,
        (SELECT COUNT(*) FROM phone_numbers) AS numbers,
        (SELECT COUNT(*) FROM voices WHERE status = 'pending_review') AS pending_voices,
        (SELECT COUNT(*) FROM calls WHERE status IN ('in-progress','dialing')) AS live_calls""")
    data["plans"] = db.query("SELECT [plan] AS name, COUNT(*) AS value FROM tenants GROUP BY [plan]")
    return data


@router.get("/tenants")
def tenants(q: str = "", a=Depends(require_admin)):
    month = db.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return db.query("""SELECT t.*, (SELECT COUNT(*) FROM users u WHERE u.tenant_id = t.id AND u.is_active = 1) AS users,
        (SELECT COUNT(*) FROM agents g WHERE g.tenant_id = t.id) AS agents,
        (SELECT COUNT(*) FROM phone_numbers p WHERE p.tenant_id = t.id) AS numbers,
        (SELECT COUNT(*) FROM calls c WHERE c.tenant_id = t.id) AS calls,
        (SELECT ISNULL(SUM(duration_sec), 0) / 60.0 FROM calls c WHERE c.tenant_id = t.id AND c.started_at >= ?) AS minutes_month,
        (SELECT TOP 1 email FROM users u WHERE u.tenant_id = t.id AND u.role = 'owner') AS owner_email
        FROM tenants t WHERE t.name LIKE ? ORDER BY t.created_at DESC""", month, f"%{q}%")


@router.put("/tenants/{tid}")
def update_tenant(tid: str, b: TenantUpd, a=Depends(require_admin)):
    data = {k: v for k, v in b.model_dump().items() if v is not None}
    if b.plan and b.minutes_quota is None:
        data["minutes_quota"] = PLAN_QUOTA.get(b.plan, 100)
    if data.get("max_concurrent_calls") is not None:
        data["max_concurrent_calls"] = max(1, min(int(data["max_concurrent_calls"]), 500))
    db.update("tenants", tid, data)
    hot.drop(("tenant", tid))
    db.audit(tid, a["id"], "admin.tenant.update", str(data))
    return {"ok": True}


@router.get("/calls")
def calls(page: int = 1, size: int = 25, a=Depends(require_admin)):
    total = db.scalar("SELECT COUNT(*) FROM calls")
    items = db.query("""SELECT c.*, t.name AS tenant_name, g.name AS agent_name FROM calls c
        LEFT JOIN tenants t ON t.id = c.tenant_id LEFT JOIN agents g ON g.id = c.agent_id
        ORDER BY c.started_at DESC OFFSET ? ROWS FETCH NEXT ? ROWS ONLY""", (max(page, 1) - 1) * size, min(size, 100))
    for i in items:
        for k in ("usage_json", "analysis_json", "variables_json", "metadata_json"):
            i.pop(k, None)
    return {"total": total, "items": items}


@router.get("/calls/{cid}")
def call_detail(cid: str, a=Depends(require_admin)):
    c = db.one("SELECT c.*, t.name AS tenant_name FROM calls c LEFT JOIN tenants t ON t.id = c.tenant_id WHERE c.id = ?", cid)
    if not c:
        raise HTTPException(404)
    from .calls import _detail
    return _detail(c)


@router.get("/voices")
def voices(a=Depends(require_admin)):
    return db.query("""SELECT v.id, v.name, v.locale, v.talent_name, v.company_name, v.status, v.error, v.created_at,
        v.review_note, t.name AS tenant_name FROM voices v LEFT JOIN tenants t ON t.id = v.tenant_id
        ORDER BY CASE WHEN v.status = 'pending_review' THEN 0 ELSE 1 END, v.created_at DESC""")


@router.post("/voices/{vid}/review")
def review(vid: str, b: Review, a=Depends(require_admin)):
    v = db.one("SELECT * FROM voices WHERE id = ?", vid)
    if not v:
        raise HTTPException(404)
    if b.approve:
        db.update("voices", vid, {"status": "approved", "review_note": b.note})
        spawn(speech.create_personal_voice(vid))     # was asyncio.get_event_loop() -> crashed in worker thread
    else:
        db.update("voices", vid, {"status": "rejected", "review_note": b.note})
    db.audit(v["tenant_id"], a["id"], "admin.voice.review", f"{vid} approve={b.approve}")
    return {"ok": True}


@router.get("/numbers")
def numbers(a=Depends(require_admin)):
    return db.query("""SELECT p.*, t.name AS tenant_name, g.name AS agent_name FROM phone_numbers p
        LEFT JOIN tenants t ON t.id = p.tenant_id LEFT JOIN agents g ON g.id = p.agent_id ORDER BY p.created_at DESC""")


@router.get("/audit")
def audit(a=Depends(require_admin)):
    return db.query("""SELECT TOP 300 l.action, l.detail, l.created_at, u.email, t.name AS tenant_name FROM audit_logs l
        LEFT JOIN users u ON u.id = l.user_id LEFT JOIN tenants t ON t.id = l.tenant_id ORDER BY l.created_at DESC""")


@router.get("/health")
def health(a=Depends(require_admin)):
    try:
        db.scalar("SELECT 1"); dbok = True
    except Exception:
        dbok = False
    return {"fabric_sql": dbok, "voice_live": bool(settings.VL_ENDPOINT and settings.VL_KEY),
            "acs": bool(settings.ACS_CONNECTION_STRING), "redis": bool(settings.REDIS_URL),
            "blob_storage": bool(settings.AZURE_STORAGE_CONNECTION_STRING),
            "embeddings": bool(settings.AOAI_EMBED_DEPLOYMENT),
            "speech": bool(settings.SPEECH_KEY), "azure_openai": bool(settings.AOAI_KEY),
            "vobiz": bool(settings.VOBIZ_AUTH_ID), "twilio": bool(settings.TWILIO_ACCOUNT_SID),
            "public_url": settings.PUBLIC_BASE_URL, "model": settings.VL_MODEL}
