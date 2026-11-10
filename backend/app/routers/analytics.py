import datetime

from fastapi import APIRouter, Depends

from .. import db
from ..security import current_user

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def overview_for(tenant_id: str | None, days: int):
    since = db.now() - datetime.timedelta(days=days)
    tw, tp = ("AND c.tenant_id = ?", [tenant_id]) if tenant_id else ("", [])
    k = db.one(f"""SELECT COUNT(*) AS total_calls,
               SUM(CASE WHEN c.status = 'completed' THEN 1 ELSE 0 END) AS completed,
               SUM(CASE WHEN c.status IN ('failed','no-answer','busy') THEN 1 ELSE 0 END) AS failed,
               SUM(CASE WHEN c.direction = 'inbound' THEN 1 ELSE 0 END) AS inbound,
               SUM(CASE WHEN c.direction = 'outbound' THEN 1 ELSE 0 END) AS outbound,
               ISNULL(SUM(c.duration_sec), 0) AS total_seconds, ISNULL(AVG(c.duration_sec), 0) AS avg_seconds,
               ISNULL(SUM(c.cost_usd), 0) AS cost_usd,
               SUM(CASE WHEN c.transferred = 1 THEN 1 ELSE 0 END) AS transferred
               FROM calls c WHERE c.started_at >= ? {tw}""", since, *tp) or {}
    daily = db.query(f"""SELECT CAST(c.started_at AS DATE) AS day, COUNT(*) AS calls,
                     ISNULL(SUM(c.duration_sec), 0) / 60.0 AS minutes
                     FROM calls c WHERE c.started_at >= ? {tw} GROUP BY CAST(c.started_at AS DATE) ORDER BY day""",
                     since, *tp)
    for d in daily:
        d["day"] = str(d["day"])
    sentiment = db.query(f"""SELECT ISNULL(NULLIF(c.sentiment, ''), 'unknown') AS name, COUNT(*) AS value FROM calls c
                         WHERE c.started_at >= ? {tw} GROUP BY ISNULL(NULLIF(c.sentiment, ''), 'unknown')""", since, *tp)
    by_agent = db.query(f"""SELECT TOP 8 ISNULL(a.name, 'Deleted') AS name, COUNT(*) AS calls,
                        ISNULL(AVG(c.duration_sec), 0) AS avg_sec FROM calls c LEFT JOIN agents a ON a.id = c.agent_id
                        WHERE c.started_at >= ? {tw} GROUP BY a.name ORDER BY COUNT(*) DESC""", since, *tp)
    outcomes = db.query(f"""SELECT TOP 6 c.outcome AS name, COUNT(*) AS value FROM calls c
                        WHERE c.started_at >= ? AND c.outcome IS NOT NULL AND c.outcome <> '' {tw}
                        GROUP BY c.outcome ORDER BY COUNT(*) DESC""", since, *tp)
    langs = db.query(f"""SELECT TOP 6 c.language AS name, COUNT(*) AS value FROM calls c
                     WHERE c.started_at >= ? AND c.language IS NOT NULL AND c.language <> '' {tw}
                     GROUP BY c.language ORDER BY COUNT(*) DESC""", since, *tp)
    reasons = db.query(f"""SELECT TOP 8 c.ended_reason AS name, COUNT(*) AS value FROM calls c
                       WHERE c.started_at >= ? AND c.ended_reason IS NOT NULL {tw}
                       GROUP BY c.ended_reason ORDER BY COUNT(*) DESC""", since, *tp)
    extra = db.one(f"""SELECT SUM(CASE WHEN c.in_voicemail = 1 THEN 1 ELSE 0 END) AS voicemail,
                       SUM(CASE WHEN c.call_successful = 1 THEN 1 ELSE 0 END) AS successful,
                       ISNULL(SUM(c.cost_ai_usd), 0) AS cost_ai_usd, ISNULL(SUM(c.cost_telephony_usd), 0) AS cost_telephony_usd
                       FROM calls c WHERE c.started_at >= ? {tw}""", since, *tp) or {}
    k.update(extra)
    total = k.get("total_calls") or 0
    k["success_rate"] = round((k.get("completed") or 0) * 100 / total, 1) if total else 0
    return {"kpis": k, "daily": daily, "sentiment": sentiment, "by_agent": by_agent,
            "outcomes": outcomes, "languages": langs, "end_reasons": reasons}


@router.get("/overview")
def overview(days: int = 30, u=Depends(current_user)):
    data = overview_for(u["tenant_id"], days)
    t = db.one("SELECT minutes_quota FROM tenants WHERE id = ?", u["tenant_id"])
    month = db.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = db.scalar("SELECT ISNULL(SUM(duration_sec), 0) FROM calls WHERE tenant_id = ? AND started_at >= ?",
                     u["tenant_id"], month) or 0
    data["usage"] = {"minutes_used": round(used / 60, 1), "minutes_quota": t["minutes_quota"]}
    data["counts"] = db.one("""SELECT (SELECT COUNT(*) FROM agents WHERE tenant_id = ?) AS agents,
                               (SELECT COUNT(*) FROM phone_numbers WHERE tenant_id = ?) AS numbers,
                               (SELECT COUNT(*) FROM knowledge_docs WHERE tenant_id = ?) AS docs""",
                            u["tenant_id"], u["tenant_id"], u["tenant_id"])
    data["recent"] = db.query("""SELECT TOP 6 c.id, c.direction, c.from_number, c.to_number, c.status, c.duration_sec,
                                 c.sentiment, c.started_at, a.name AS agent_name FROM calls c
                                 LEFT JOIN agents a ON a.id = c.agent_id WHERE c.tenant_id = ?
                                 ORDER BY c.started_at DESC""", u["tenant_id"])
    return data
