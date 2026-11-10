"""Post-call: AI analysis (built-in + custom fields), PII redaction, webhooks (call.started / call.ended /
call.analyzed)."""
import asyncio
import hashlib
import hmac
import json
import logging

import httpx

from .. import db
from ..config import settings
from . import netguard, pii, pricing
from .versions import jload

log = logging.getLogger("postcall")

BUILTIN = """Return JSON with these keys:
- summary: max 3 sentences
- sentiment: positive | neutral | negative
- outcome: short label, e.g. "Appointment booked", "Lead qualified", "Callback requested", "Unresolved"
- language: main language spoken
- call_successful: true if the agent achieved the goal of the call, else false
- in_voicemail: true if the call reached a voicemail / answering machine"""


def _field_spec(fields: list[dict]) -> str:
    if not fields:
        return ""
    lines = ["\nAlso return a key \"custom\" - an object with exactly these keys (use null when not mentioned):"]
    for f in fields:
        t = f.get("type") or "string"
        extra = f" one of: {', '.join(f.get('options') or [])}" if t == "enum" else ""
        lines.append(f"- {f['name']} ({t}{extra}): {f.get('description') or ''}")
    return "\n".join(lines)


async def _chat(messages: list[dict], max_tokens: int = 700, json_mode: bool = True) -> tuple[str, dict]:
    url = (f"{settings.AOAI_ENDPOINT}/openai/deployments/{settings.AOAI_DEPLOYMENT}/chat/completions"
           f"?api-version={settings.AOAI_API_VERSION}")
    body = {"messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(url, json=body, headers={"api-key": settings.AOAI_KEY})
        r.raise_for_status()
        j = r.json()
        return j["choices"][0]["message"]["content"], j.get("usage") or {}


async def analyse(transcript: str, fields: list[dict]) -> tuple[dict, float]:
    if not (settings.AOAI_ENDPOINT and settings.AOAI_KEY) or not transcript.strip():
        return {}, 0.0
    content, usage = await _chat([
        {"role": "system", "content": "You analyse phone call transcripts. " + BUILTIN + _field_spec(fields)},
        {"role": "user", "content": transcript[:14000]}])
    cost = pricing.analysis_cost(usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0)
    return json.loads(content), cost


async def quick_summary(turns: list[dict], reason: str) -> str:
    """One or two sentences for a warm-transfer briefing. Never blocks the call for more than ~4 s."""
    text = "\n".join(f"{t['role'].upper()}: {t['text']}" for t in turns[-30:])
    if not (settings.AOAI_ENDPOINT and settings.AOAI_KEY) or not text:
        return f"Transferring a caller. Reason: {reason or 'the caller asked for a person'}."
    try:
        content, _ = await asyncio.wait_for(_chat([
            {"role": "system", "content": "Write a spoken briefing for a human colleague who is about to take over "
                                          "this phone call: who the caller is (name if known) and what they need, "
                                          "in at most two short sentences. Plain text only."},
            {"role": "user", "content": text[:6000]}], max_tokens=120, json_mode=False), 4)
        return content.strip()
    except Exception:
        return f"Transferring a caller. Reason: {reason or 'the caller asked for a person'}."


# ---------------- webhooks ----------------

def _wants(h: dict, event: str) -> bool:
    ev = [e.strip() for e in (h.get("events") or "").split(",") if e.strip()]
    return "*" in ev or event in ev


def signature(secret: str, body: str) -> str:
    return hmac.new((secret or "").encode(), body.encode(), hashlib.sha256).hexdigest()


async def deliver(h: dict, event: str, payload: dict) -> int | None:
    body = json.dumps({"event": event, "data": payload}, default=str)
    status, err = None, None
    for attempt in range(3):
        try:
            r = await netguard.request("POST", h["url"], content=body, timeout=10,
                                       headers={"Content-Type": "application/json", "X-UBTI-Event": event,
                                                "X-UBTI-Signature": signature(h.get("secret"), body)})
            status = r.status_code
            if status < 500:
                break
        except Exception as e:
            err = str(e)[:400]
        await asyncio.sleep(1.5 * (attempt + 1))
    ok = status is not None and status < 300
    await asyncio.to_thread(db.execute, "UPDATE webhooks SET last_status = ?, last_fired_at = ?, last_error = ? "
                                        "WHERE id = ?", "ok" if ok else "failed", db.now(),
                            None if ok else (err or f"HTTP {status}"), h["id"])
    return status


async def fire(tenant_id: str, event: str, payload: dict):
    try:
        hooks = await asyncio.to_thread(db.query, "SELECT * FROM webhooks WHERE tenant_id = ? AND active = 1", tenant_id)
        await asyncio.gather(*[deliver(h, event, payload) for h in hooks if _wants(h, event)])
    except Exception:
        log.exception("webhook delivery failed")


def public_call(call: dict) -> dict:
    c = {k: v for k, v in call.items() if k not in ("usage_json",)}
    for k in ("analysis_json", "variables_json", "metadata_json"):
        if k in c:
            c[k.replace("_json", "")] = jload(c.pop(k), {})
    return c


# ---------------- after the call ----------------

async def process(call_id: str, agent: dict | None = None, in_voicemail: bool = False):
    try:
        call = await asyncio.to_thread(db.one, "SELECT * FROM calls WHERE id = ?", call_id)
        if not call:
            return
        turns = await asyncio.to_thread(db.query, "SELECT role, text, offset_ms FROM call_transcripts WHERE call_id = ? "
                                                  "ORDER BY offset_ms", call_id)
        t = await asyncio.to_thread(db.one, "SELECT pii_redaction FROM tenants WHERE id = ?", call["tenant_id"])
        shown = [{**x, "text": pii.redact(x["text"])} for x in turns] if t and t.get("pii_redaction") else turns
        await fire(call["tenant_id"], "call.ended", {**public_call(call), "transcript": shown})
        await analyse_call(call, turns, agent, in_voicemail)
    except Exception:
        log.exception("postcall failed")


async def analyse_call(call: dict, turns: list[dict] | None = None, agent: dict | None = None,
                       in_voicemail: bool = False):
    cid = call["id"]
    if turns is None:
        turns = await asyncio.to_thread(db.query, "SELECT role, text, offset_ms FROM call_transcripts WHERE call_id = ? "
                                                  "ORDER BY offset_ms", cid)
    if agent is None:
        row = await asyncio.to_thread(db.one, "SELECT analysis_fields_json, published_json FROM agents WHERE id = ?",
                                      call.get("agent_id"))
        pub = jload((row or {}).get("published_json"), {})
        agent = {"analysis_fields_json": pub.get("analysis_fields_json") or (row or {}).get("analysis_fields_json")}
    fields = [f for f in jload(agent.get("analysis_fields_json"), []) if f.get("name")]
    text = "\n".join(f"{t['role'].upper()}: {t['text']}" for t in turns if t["role"] in ("user", "agent", "dtmf"))
    try:
        a, cost = await analyse(text, fields)
    except Exception as e:
        log.warning("analysis failed: %s", e)
        a, cost = {}, 0.0
    upd = {}
    if a:
        upd = {"summary": str(a.get("summary", ""))[:2000], "sentiment": str(a.get("sentiment", ""))[:20],
               "outcome": str(a.get("outcome", ""))[:100], "language": str(a.get("language", ""))[:30],
               "call_successful": bool(a.get("call_successful")),
               "in_voicemail": bool(in_voicemail or a.get("in_voicemail")),
               "analysis_json": json.dumps({"custom": a.get("custom") or {}, "call_successful": a.get("call_successful"),
                                            "in_voicemail": bool(in_voicemail or a.get("in_voicemail"))})}
        if cost:
            upd["cost_ai_usd"] = round((call.get("cost_ai_usd") or 0) + cost, 5)
            upd["cost_usd"] = round((call.get("cost_usd") or 0) + cost, 5)
    tenant = await asyncio.to_thread(db.one, "SELECT pii_redaction FROM tenants WHERE id = ?", call["tenant_id"])
    if tenant and tenant.get("pii_redaction"):
        for t in await asyncio.to_thread(db.query, "SELECT id, text FROM call_transcripts WHERE call_id = ?", cid):
            red = pii.redact(t["text"])
            if red != t["text"]:
                await asyncio.to_thread(db.execute, "UPDATE call_transcripts SET text = ? WHERE id = ?", red, t["id"])
        if upd.get("summary"):
            upd["summary"] = pii.redact(upd["summary"])
    if upd:
        await asyncio.to_thread(db.update, "calls", cid, upd)
    call = await asyncio.to_thread(db.one, "SELECT * FROM calls WHERE id = ?", cid)
    turns = await asyncio.to_thread(db.query, "SELECT role, text, offset_ms FROM call_transcripts WHERE call_id = ? "
                                              "ORDER BY offset_ms", cid)
    await fire(call["tenant_id"], "call.analyzed", {**public_call(call), "transcript": turns})
