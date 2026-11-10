import asyncio
import csv
import io
import json
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel

from .. import db
from ..security import check_sig, current_user, member, sign
from ..services import bus, hot, postcall, providers, storage
from ..services.templating import clean_vars
from ..services.versions import jload

router = APIRouter(prefix="/api/calls", tags=["calls"])
log = logging.getLogger("calls")


class OutboundIn(BaseModel):
    agent_id: str
    phone_number_id: str
    to: str
    variables: Optional[dict[str, Any]] = None      # {{name}} values for this call
    metadata: Optional[dict[str, Any]] = None       # stored with the call and sent back in webhooks


def _filters(u, agent_id, status, direction, q, reason=""):
    where, p = ["c.tenant_id = ?"], [u["tenant_id"]]
    if agent_id:
        where.append("c.agent_id = ?"); p.append(agent_id)
    if status:
        where.append("c.status = ?"); p.append(status)
    if direction:
        where.append("c.direction = ?"); p.append(direction)
    if reason:
        where.append("c.ended_reason = ?"); p.append(reason)
    if q:
        where.append("(c.from_number LIKE ? OR c.to_number LIKE ? OR c.summary LIKE ?)"); p += [f"%{q}%"] * 3
    return " AND ".join(where), p


LIST_COLS = ("c.id, c.agent_id, c.direction, c.provider, c.from_number, c.to_number, c.status, c.started_at, "
             "c.duration_sec, c.sentiment, c.outcome, c.cost_usd, c.ended_reason, c.in_voicemail, "
             "c.call_successful, c.transferred, c.campaign_id, c.agent_version, "
             "CASE WHEN c.recording_path IS NULL THEN 0 ELSE 1 END AS has_recording")


@router.get("")
def list_calls(page: int = 1, size: int = 20, agent_id: str = "", status: str = "", direction: str = "",
               q: str = "", reason: str = "", u=Depends(current_user)):
    w, p = _filters(u, agent_id, status, direction, q, reason)
    total = db.scalar(f"SELECT COUNT(*) FROM calls c WHERE {w}", *p)
    rows = db.query(f"""SELECT {LIST_COLS}, a.name AS agent_name FROM calls c LEFT JOIN agents a ON a.id = c.agent_id
                        WHERE {w} ORDER BY c.started_at DESC OFFSET ? ROWS FETCH NEXT ? ROWS ONLY""",
                    *p, (max(page, 1) - 1) * size, min(size, 100))
    return {"total": total, "items": rows}


@router.get("/export")
def export(u=Depends(current_user)):
    rows = db.query("""SELECT c.id, c.started_at, a.name AS agent, c.agent_version, c.direction, c.provider,
                       c.from_number, c.to_number, c.status, c.ended_reason, c.duration_sec, c.sentiment, c.outcome,
                       c.call_successful, c.in_voicemail, c.summary, c.analysis_json, c.variables_json,
                       c.cost_ai_usd, c.cost_telephony_usd, c.cost_usd
                       FROM calls c LEFT JOIN agents a ON a.id = c.agent_id WHERE c.tenant_id = ?
                       ORDER BY c.started_at DESC""", u["tenant_id"])
    buf = io.StringIO()
    if rows:
        custom = sorted({k for r in rows for k in (jload(r.get("analysis_json"), {}).get("custom") or {})})
        cols = [k for k in rows[0] if k not in ("analysis_json",)] + custom
        w = csv.DictWriter(buf, fieldnames=cols)
        w.writeheader()
        for r in rows:
            c = jload(r.pop("analysis_json"), {}).get("custom") or {}
            w.writerow({**r, **{k: c.get(k) for k in custom}})
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=calls.csv"})


def _detail(c: dict) -> dict:
    c["transcript"] = db.query("SELECT role, text, offset_ms FROM call_transcripts WHERE call_id = ? "
                               "ORDER BY offset_ms", c["id"])
    c["analysis"] = jload(c.pop("analysis_json", None), {})
    c["variables"] = jload(c.pop("variables_json", None), {})
    c["metadata"] = jload(c.pop("metadata_json", None), {})
    c["usage"] = jload(c.pop("usage_json", None), {})
    c["has_recording"] = bool(c.pop("recording_path", None))
    return c


@router.get("/{call_id}")
def get_call(call_id: str, u=Depends(current_user)):
    c = db.one("SELECT c.*, a.name AS agent_name FROM calls c LEFT JOIN agents a ON a.id = c.agent_id "
               "WHERE c.id = ? AND c.tenant_id = ?", call_id, u["tenant_id"])
    if not c:
        raise HTTPException(404, "Call not found")
    return _detail(c)


@router.get("/{call_id}/recording-url")
def recording_url(call_id: str, u=Depends(current_user)):
    """Short-lived link the <audio> player can use directly (no auth header needed, supports seeking)."""
    c = db.one("SELECT recording_path FROM calls WHERE id = ? AND tenant_id = ?", call_id, u["tenant_id"])
    if not c or not c.get("recording_path"):
        raise HTTPException(404, "No recording for this call")
    url = storage.signed_url(c["recording_path"])
    if url:
        return {"url": url}
    exp = int(time.time()) + 1800
    return {"url": f"/recordings/{call_id}?exp={exp}&sig={sign(f'{call_id}:{exp}')}"}


@router.post("/outbound")
async def outbound(b: OutboundIn, u=Depends(member)):
    t0 = time.time()
    num = None
    for attempt in (0, 1):   # 2nd attempt forces a fresh read (a number added a few seconds ago)
        num = next((n for n in await hot.numbers(force=attempt == 1)
                    if n["id"] == b.phone_number_id and n["tenant_id"] == u["tenant_id"]), None)
        if num:
            break
    agent = await hot.get_agent(b.agent_id)
    if not num or not agent or agent["tenant_id"] != u["tenant_id"]:
        raise HTTPException(404, "Number or agent not found")
    if agent.get("status") != "active":
        raise HTTPException(400, "This agent is paused - activate it first")
    err = await hot.capacity_error(u["tenant_id"])
    if err:
        raise HTTPException(429, "Concurrent call limit or monthly minutes reached")
    to = "+" + "".join(ch for ch in b.to if ch.isdigit())
    if len(to) < 8:
        raise HTTPException(400, "Enter the number in international format, e.g. +9198XXXXXXXX")
    call = {"id": db.new_id(), "tenant_id": u["tenant_id"], "agent_id": agent["id"], "phone_number_id": num["id"],
            "provider": num["provider"], "direction": "outbound", "from_number": num["number"],
            "to_number": to, "status": "dialing", "started_at": db.now(), "agent_version": agent.get("version"),
            "variables_json": json.dumps(clean_vars(b.variables)), "metadata_json": json.dumps(b.metadata or {})}
    hot.insert_call_bg(call)
    await bus.live_add(u["tenant_id"], call["id"], {"direction": "outbound", "to": to, "from": num["number"],
                                                     "agent": agent.get("name"), "status": "dialing",
                                                     "provider": num["provider"]})
    t1 = time.time()
    try:
        sid = await providers.place_call(u["tenant_id"], num["provider"], num["number"], to, call["id"], agent)
    except Exception as e:
        hot.db_update_bg(call["id"], {"status": "failed", "error": str(e)[:500], "ended_at": db.now(),
                                      "ended_reason": "dial_error"})
        hot.finish(call["id"])
        await bus.live_remove(u["tenant_id"], call["id"])
        raise HTTPException(400, f"Could not place call: {e}")
    log.info("[%s] outbound to %s: our checks %.2fs, %s accepted the call after %.2fs",
             call["id"][:8], to, t1 - t0, num["provider"], time.time() - t1)
    if sid:
        call["provider_call_id"] = sid
        hot.db_update_bg(call["id"], {"provider_call_id": sid})
    await asyncio.to_thread(db.audit, u["tenant_id"], u["id"], "call.outbound", to)
    return {"id": call["id"], "status": "dialing"}


@router.post("/{call_id}/analyze")
async def rerun_analysis(call_id: str, u=Depends(member)):
    c = await asyncio.to_thread(db.one, "SELECT * FROM calls WHERE id = ? AND tenant_id = ?", call_id, u["tenant_id"])
    if not c:
        raise HTTPException(404)
    await postcall.analyse_call(c, in_voicemail=bool(c.get("in_voicemail")))
    return await asyncio.to_thread(get_call, call_id, u)


@router.post("/{call_id}/end")
async def end_call(call_id: str, u=Depends(member)):
    """End a live call from the dashboard (works on any replica)."""
    c = await asyncio.to_thread(db.one, "SELECT id, tenant_id FROM calls WHERE id = ? AND tenant_id = ?", call_id,
                                u["tenant_id"])
    if not c:
        raise HTTPException(404)
    await bus.control(call_id, "end")
    await asyncio.to_thread(db.audit, u["tenant_id"], u["id"], "call.end", call_id)
    return {"ok": True}


@router.delete("/{call_id}")
def delete_call(call_id: str, u=Depends(member)):
    c = db.one("SELECT recording_path FROM calls WHERE id = ? AND tenant_id = ?", call_id, u["tenant_id"])
    if not c:
        return {"ok": True}
    if c.get("recording_path"):
        storage.delete(c["recording_path"])
    db.execute("DELETE FROM call_transcripts WHERE call_id = ?", call_id)
    db.execute("UPDATE campaign_contacts SET call_id = NULL WHERE call_id = ?", call_id)
    db.execute("DELETE FROM calls WHERE id = ? AND tenant_id = ?", call_id, u["tenant_id"])
    db.audit(u["tenant_id"], u["id"], "call.delete", call_id)
    return {"ok": True}


# Served at /recordings/{id} (outside /api) for local storage; Blob storage uses SAS links instead.
files_router = APIRouter(tags=["calls"])


@files_router.get("/recordings/{call_id}")
def recording_file(call_id: str, exp: int, sig: str):
    if exp < time.time() or not check_sig(f"{call_id}:{exp}", sig):
        raise HTTPException(403, "Link expired")
    c = db.one("SELECT recording_path FROM calls WHERE id = ?", call_id)
    if not c or not c.get("recording_path"):
        raise HTTPException(404)
    path = storage.local_path(c["recording_path"])
    if path:
        return FileResponse(path, media_type="audio/mpeg" if path.endswith(".mp3") else "audio/wav")
    url = storage.signed_url(c["recording_path"])
    return RedirectResponse(url) if url else Response(status_code=404)
