"""Background jobs, driven by the database so they survive restarts and work with several replicas:

- batch calls: claim contacts inside the calling window, respect concurrency, retry no-answer / voicemail
- stuck calls: 'dialing' rows the carrier never reported back
- data retention: delete calls, transcripts and recordings older than the tenant's retention period
"""
import asyncio
import datetime
import json
import logging

from .. import db
from . import bus, hot, providers, storage
from .versions import jload

log = logging.getLogger("scheduler")
TICK = 5
DONE = ("completed", "failed", "no-answer", "busy", "canceled")


def in_window(c: dict, tz_default: str | None) -> bool:
    if not (c.get("window_start") and c.get("window_end")):
        days = c.get("window_days")
        if not days:
            return True
    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo(c.get("timezone") or tz_default or "Asia/Kolkata"))
    except Exception:
        now = datetime.datetime.utcnow()
    days = [int(d) for d in (c.get("window_days") or "0,1,2,3,4,5,6").split(",") if d.strip().isdigit()]
    if now.weekday() not in days:            # 0 = Monday
        return False
    if c.get("window_start") and c.get("window_end"):
        hm = now.strftime("%H:%M")
        s, e = c["window_start"], c["window_end"]
        return s <= hm < e if s <= e else (hm >= s or hm < e)     # windows past midnight
    return True


def _resolve_finished_contacts():
    """Contacts whose call has ended: done, retry later, or failed."""
    rows = db.query("""SELECT cc.id, cc.campaign_id, cc.attempts, c.status AS call_status, c.in_voicemail,
                              c.ended_reason, cp.max_attempts, cp.retry_delay_min
                       FROM campaign_contacts cc JOIN calls c ON c.id = cc.call_id
                       JOIN campaigns cp ON cp.id = cc.campaign_id
                       WHERE cc.status = 'calling' AND c.status IN ('completed','failed','no-answer','busy','canceled')
                         AND (c.status <> 'completed' OR c.ended_at IS NOT NULL)""")
    for r in rows:
        reached = r["call_status"] == "completed" and not r["in_voicemail"] and r["ended_reason"] not in ("ivr",)
        if reached:
            db.execute("UPDATE campaign_contacts SET status = 'done' WHERE id = ?", r["id"])
        elif (r["attempts"] or 0) < (r["max_attempts"] or 1):
            nxt = db.now() + datetime.timedelta(minutes=r["retry_delay_min"] or 60)
            db.execute("UPDATE campaign_contacts SET status = 'retry', next_attempt_at = ?, last_error = ? WHERE id = ?",
                       nxt, r["ended_reason"] or r["call_status"], r["id"])
        else:
            db.execute("UPDATE campaign_contacts SET status = 'failed', last_error = ? WHERE id = ?",
                       r["ended_reason"] or r["call_status"], r["id"])


def _refresh_counts(cid: str):
    db.execute("""UPDATE campaigns SET
                    completed = (SELECT COUNT(*) FROM campaign_contacts WHERE campaign_id = ? AND status = 'done'),
                    failed = (SELECT COUNT(*) FROM campaign_contacts WHERE campaign_id = ? AND status = 'failed')
                  WHERE id = ?""", cid, cid, cid)


def _claim(cid: str, n: int) -> list[dict]:
    return db.query(f"""UPDATE TOP ({int(n)}) campaign_contacts
                        SET status = 'calling', attempts = ISNULL(attempts, 0) + 1, last_attempt_at = SYSUTCDATETIME()
                        OUTPUT inserted.*
                        WHERE campaign_id = ? AND status IN ('pending', 'retry')
                          AND (next_attempt_at IS NULL OR next_attempt_at <= SYSUTCDATETIME())""", cid)


async def _dial(c: dict, num: dict, agent: dict, ct: dict):
    variables = {**jload(ct.get("variables_json"), {})}
    if ct.get("name"):
        variables.setdefault("customer_name", ct["name"])
        variables.setdefault("name", ct["name"])
    call = {"id": db.new_id(), "tenant_id": c["tenant_id"], "agent_id": c["agent_id"], "phone_number_id": num["id"],
            "provider": num["provider"], "direction": "outbound", "from_number": num["number"],
            "to_number": ct["phone"], "status": "dialing", "campaign_id": c["id"], "started_at": db.now(),
            "variables_json": json.dumps(variables), "agent_version": agent.get("version")}
    await asyncio.to_thread(db.insert, "calls", call)
    await asyncio.to_thread(db.execute, "UPDATE campaign_contacts SET call_id = ? WHERE id = ?", call["id"], ct["id"])
    await bus.live_add(c["tenant_id"], call["id"], {"direction": "outbound", "to": ct["phone"], "status": "dialing",
                                                     "agent": agent.get("name"), "campaign": c["name"]})
    try:
        pid = await providers.place_call(c["tenant_id"], num["provider"], num["number"], ct["phone"], call["id"], agent)
        if pid:
            await asyncio.to_thread(db.update, "calls", call["id"], {"provider_call_id": pid})
    except Exception as e:
        log.warning("[%s] dial %s failed: %s", c["id"][:8], ct["phone"], e)
        await asyncio.to_thread(db.update, "calls", call["id"], {"status": "failed", "error": str(e)[:400],
                                                                  "ended_at": db.now(), "ended_reason": "dial_error"})
        await bus.live_remove(c["tenant_id"], call["id"])


async def run_campaigns():
    await asyncio.to_thread(_resolve_finished_contacts)
    camps = await asyncio.to_thread(db.query, "SELECT * FROM campaigns WHERE status = 'running'")
    for c in camps:
        try:
            await asyncio.to_thread(_refresh_counts, c["id"])
            left = await asyncio.to_thread(db.one, """SELECT
                    SUM(CASE WHEN status IN ('pending','retry') THEN 1 ELSE 0 END) AS waiting,
                    SUM(CASE WHEN status = 'calling' THEN 1 ELSE 0 END) AS calling
                    FROM campaign_contacts WHERE campaign_id = ?""", c["id"])
            waiting, calling = (left or {}).get("waiting") or 0, (left or {}).get("calling") or 0
            if not waiting and not calling:
                await asyncio.to_thread(db.update, "campaigns", c["id"], {"status": "completed",
                                                                           "finished_at": db.now()})
                continue
            t = await hot.tenant(c["tenant_id"])
            if not in_window(c, (t or {}).get("timezone")):
                continue
            agent = await hot.get_agent(c["agent_id"])
            num = next((n for n in await hot.numbers() if n["id"] == c["phone_number_id"]), None)
            if not agent or not num or agent.get("status") != "active":
                await asyncio.to_thread(db.update, "campaigns", c["id"], {"status": "paused"})
                log.warning("campaign %s paused: agent inactive or number missing", c["id"][:8])
                continue
            if await hot.capacity_error(c["tenant_id"]):
                continue
            limit = (t or {}).get("max_concurrent_calls") or 5
            free = min((c.get("concurrency") or 1) - calling, limit - await bus.live_count(c["tenant_id"]))
            if free <= 0:
                continue
            for ct in await asyncio.to_thread(_claim, c["id"], free):
                asyncio.create_task(_dial(c, num, agent, ct))
        except Exception:
            log.exception("campaign %s tick failed", c["id"][:8])


def _stuck_calls() -> list[dict]:
    """Carrier never reported back: dialing for 5+ minutes -> no answer."""
    cutoff = db.now() - datetime.timedelta(minutes=5)
    return db.query("UPDATE calls SET status = 'no-answer', ended_at = SYSUTCDATETIME(), ended_reason = 'no_answer' "
                    "OUTPUT inserted.id, inserted.tenant_id WHERE status = 'dialing' AND started_at < ?", cutoff)


def _retention():
    for t in db.query("SELECT id, retention_days FROM tenants WHERE retention_days > 0"):
        cutoff = db.now() - datetime.timedelta(days=int(t["retention_days"]))
        old = db.query("SELECT TOP 500 id, recording_path FROM calls WHERE tenant_id = ? AND started_at < ? "
                       "AND status NOT IN ('in-progress', 'dialing')", t["id"], cutoff)
        for c in old:
            if c.get("recording_path"):
                storage.delete(c["recording_path"])
            db.execute("DELETE FROM call_transcripts WHERE call_id = ?", c["id"])
            db.execute("UPDATE campaign_contacts SET call_id = NULL WHERE call_id = ?", c["id"])
            db.execute("DELETE FROM calls WHERE id = ?", c["id"])
        if old:
            log.info("retention: deleted %d calls of tenant %s", len(old), t["id"][:8])


async def loop():
    n = 0
    while True:
        await asyncio.sleep(TICK)
        n += 1
        try:
            if not await bus.lock("scheduler", TICK - 0.5):     # one replica runs each tick
                continue
            await run_campaigns()
            if n % 12 == 0:
                for c in await asyncio.to_thread(_stuck_calls):
                    await bus.live_remove(c["tenant_id"], c["id"])      # free the concurrency slot
            if n % 720 == 0:                                      # about hourly
                await asyncio.to_thread(_retention)
        except Exception:
            log.exception("scheduler tick failed")
