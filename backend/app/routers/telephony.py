"""Telephony webhooks (Vobiz / Twilio / Azure Communication Services) + media WebSockets + browser test call.

Every URL we give a provider is signed: outbound calls carry ?call_id=..&sig=.., inbound number webhooks carry
the tenant's key (?k=..), and media WebSockets have the signature in the path. Requests without a valid
signature are rejected (set ALLOW_UNSIGNED_WEBHOOKS=true only while migrating old number configs).

The answer webhook is on the hot path: the phone provider waits only a few seconds for it. It therefore
uses in-memory data (see services/hot.py) and never waits for the database.
"""
import asyncio
import json
import logging
import re
import time
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, Response

from .. import db
from ..config import settings
from ..security import check_sig, platform_key, user_from_token
from ..services import bus, hot, providers, voicelive
from ..services.templating import clean_vars
from ..services.voicelive import run_bridge, signal_end

router = APIRouter(tags=["telephony"])
log = logging.getLogger("telephony")
MACHINE = {"machine_start", "machine_end_beep", "machine_end_silence", "machine_end_other", "fax"}
_NOT_ENDED = {"ringing", "in-progress", "inprogress", "answered", "queued", "initiated", "early-media",
              "early_media", "started", "start"}


def xml(body: str) -> Response:
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>', media_type="application/xml")


def _say(provider: str, text: str) -> str:
    return f"<Speak>{escape(text)}</Speak>" if provider == "vobiz" else f"<Say>{escape(text)}</Say>"


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


async def _params(req: Request) -> dict:
    p = dict(req.query_params)
    try:
        p.update({k: str(v) for k, v in (await req.form()).items()})
    except Exception:
        pass
    return p


def _require_sig(p: dict) -> str:
    cid = p.get("call_id") or ""
    if not cid or not check_sig(cid, p.get("sig")):
        raise HTTPException(403, "bad signature")
    return cid


async def _tenant_key_ok(tenant_id: str, key: str | None, allow_platform: bool = False) -> bool:
    if allow_platform and key and key == platform_key():     # only the platform's own ACS resource uses it
        return True
    t = await hot.tenant(tenant_id)
    if key and t and t.get("webhook_key") and key == t["webhook_key"]:
        return True
    if settings.ALLOW_UNSIGNED_WEBHOOKS and not key:
        return True
    return False


async def _match_number(provider: str, to: str):
    if not to:
        return None
    for attempt in (0, 1):   # 2nd attempt forces a fresh read (a number added a few seconds ago)
        for n in await hot.numbers(force=attempt == 1):
            if n["provider"] == provider and _digits(n["number"])[-10:] == to[-10:]:
                return n
    return None


async def _new_inbound(provider: str, num: dict, agent: dict, frm: str, pid: str | None) -> dict:
    call = {"id": db.new_id(), "tenant_id": num["tenant_id"], "agent_id": num["agent_id"],
            "phone_number_id": num["id"], "provider": provider, "direction": "inbound",
            "from_number": frm, "to_number": num["number"], "status": "in-progress",
            "provider_call_id": pid, "started_at": db.now(), "answered_at": db.now(),
            "agent_version": agent.get("version")}
    hot.insert_call_bg(call)
    await bus.live_add(num["tenant_id"], call["id"], {"direction": "inbound", "from": frm, "to": num["number"],
                                                       "agent": agent.get("name"), "status": "in-progress",
                                                       "provider": provider})
    return call


async def _inbound_checks(provider: str, to: str, key: str | None, allow_platform: bool = False):
    """Returns (num, agent, None) or (None, None, message to speak)."""
    num = await _match_number(provider, to)
    if not num or not num.get("agent_id"):
        log.warning("inbound %s call to %s: number not configured or no agent assigned", provider, to)
        return None, None, "Sorry, this number is not configured yet. Goodbye."
    if not await _tenant_key_ok(num["tenant_id"], key, allow_platform):
        raise HTTPException(403, "bad webhook key")
    agent = await hot.get_agent(num["agent_id"])
    if not agent or agent.get("status") != "active":
        return None, None, "Sorry, this service is not available right now."
    err = await hot.capacity_error(num["tenant_id"])
    if err:
        return None, None, err
    return num, agent, None


def _stream_xml(provider: str, call_id: str) -> Response:
    ws = providers.media_url(provider, call_id)
    if provider == "twilio":
        return xml(f'<Connect action="{escape(providers.cb("/telephony/twilio/after", call_id))}">'
                   f'<Stream url="{escape(ws)}"/></Connect>')
    return xml(f'<Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-l16;rate=16000">'
               f'{escape(ws)}</Stream><Hangup/>')


# ---------------- Vobiz / Twilio ----------------

@router.api_route("/telephony/{provider}/answer", methods=["GET", "POST"])
async def answer(provider: str, req: Request):
    if provider not in ("vobiz", "twilio"):
        raise HTTPException(404)
    t0 = time.time()
    p = await _params(req)
    log.info("answer webhook (%s): call_id=%s From=%s To=%s", provider, p.get("call_id"), p.get("From"), p.get("To"))
    if p.get("call_id"):  # outbound call that we placed
        call_id = _require_sig(p)
        call = await hot.get_call(call_id)
        if not call:
            log.warning("answer webhook for unknown call %s", call_id)
            return xml("<Hangup/>")
        agent = await hot.get_agent(call["agent_id"])
        if not agent:
            return xml(_say(provider, "Sorry, this service is not available right now.") + "<Hangup/>")
        call = {**call, "provider_call_id": call.get("provider_call_id") or p.get("CallUUID") or p.get("CallSid")}
        hot.CALLS[call_id] = call
        hot.db_update_bg(call_id, {"status": "in-progress", "answered_at": db.now()})
        await bus.live_add(call["tenant_id"], call_id, {"direction": "outbound", "to": call["to_number"],
                                                         "from": call["from_number"], "agent": agent.get("name"),
                                                         "status": "in-progress", "provider": provider})
    else:  # inbound
        num, agent, msg = await _inbound_checks(provider, _digits(p.get("To")), p.get("k"))
        if msg:
            return xml(_say(provider, msg) + "<Hangup/>")
        call = await _new_inbound(provider, num, agent, p.get("From", ""), p.get("CallUUID") or p.get("CallSid"))
        call_id = call["id"]
    voicelive.start_prep(call_id, agent, provider, call)     # open Voice Live now, while the provider connects
    log.info("[%s] answer webhook ready in %.2fs", call_id[:8], time.time() - t0)
    return _stream_xml(provider, call_id)


@router.api_route("/telephony/{provider}/status", methods=["GET", "POST"])
async def status(provider: str, req: Request):
    p = await _params(req)
    call_id = p.get("call_id")
    log.info("status webhook (%s): %s", provider,
             {k: v for k, v in p.items() if k.lower() in ("call_id", "callstatus", "hangupcause", "event",
                                                          "duration", "calluuid", "callsid", "from", "to")})
    if call_id:
        _require_sig(p)
    else:
        pid = p.get("CallUUID") or p.get("CallSid") or p.get("call_uuid")
        row = pid and await asyncio.to_thread(db.one, "SELECT id, tenant_id FROM calls WHERE provider_call_id = ?", pid)
        if not row:
            return {"ok": True}
        if not await _tenant_key_ok(row["tenant_id"], p.get("k")):
            raise HTTPException(403, "bad webhook key")
        call_id = row["id"]
    st = (p.get("CallStatus") or "").lower()
    if st not in _NOT_ENDED:
        await signal_end(call_id)  # caller hung up -> stop the AI bridge (wherever it runs)
    mapped = {"no-answer": "no-answer", "busy": "busy", "failed": "failed", "canceled": "canceled",
              "no_answer": "no-answer", "user_busy": "busy", "no_user_response": "no-answer"}.get(st) or \
        {"no_user_response": "no-answer", "user_busy": "busy", "no_answer": "no-answer"}.get(
            (p.get("HangupCause") or "").lower())
    if st not in _NOT_ENDED and not await bus.kv_get(f"bridge:{call_id}", as_json=False):
        # ended before our media bridge ran (no answer, busy, failed...) - free the concurrency slot
        if mapped:
            hot.db_update_bg(call_id, {"status": mapped, "ended_at": db.now(), "ended_reason": mapped.replace("-", "_")})
        call = await hot.get_call(call_id)
        if call:
            await bus.live_remove(call["tenant_id"], call_id)
    return {"ok": True}


@router.api_route("/telephony/{provider}/amd", methods=["GET", "POST"])
async def amd(provider: str, req: Request):
    """Carrier answering-machine detection result (Twilio AsyncAmd / Vobiz machine_detection_url)."""
    p = await _params(req)
    call_id = _require_sig(p)
    machine = (p.get("AnsweredBy") or "").lower() in MACHINE if provider == "twilio" else \
        str(p.get("Machine") or "").lower() == "true"
    log.info("[%s] answering-machine detection (%s): %s", call_id[:8], provider, p.get("AnsweredBy") or p.get("Machine"))
    if machine:
        await bus.control(call_id, "voicemail", source=f"{provider} detection")
    return {"ok": True}


@router.api_route("/telephony/twilio/after", methods=["GET", "POST"])
async def twilio_after(req: Request):
    """Twilio fetches this when our media stream ends: dial the transfer target, or hang up."""
    call_id = _require_sig(await _params(req))
    t = await bus.kv_get(f"transfer:{call_id}")
    if t and t.get("number"):
        whisper = providers.cb("/telephony/twilio/whisper", call_id) if t.get("mode") == "warm" else None
        return xml(providers.dial_xml("twilio", t, whisper))
    return xml("<Hangup/>")


@router.api_route("/telephony/vobiz/transfer", methods=["GET", "POST"])
async def vobiz_transfer(req: Request):
    call_id = _require_sig(await _params(req))
    t = await bus.kv_get(f"transfer:{call_id}")
    if not t:
        return xml("<Hangup/>")
    whisper = providers.cb("/telephony/vobiz/whisper", call_id) if t.get("mode") == "warm" else None
    return xml(providers.dial_xml("vobiz", t, whisper))


@router.api_route("/telephony/{provider}/whisper", methods=["GET", "POST"])
async def whisper(provider: str, req: Request):
    """Played to the human who answers a warm transfer, before the caller is connected."""
    call_id = _require_sig(await _params(req))
    t = await bus.kv_get(f"transfer:{call_id}") or {}
    return xml(_say(provider, t.get("summary") or "Incoming transferred call."))


# ---------------- Azure Communication Services ----------------

def _acs_number(ident: dict) -> str:
    ident = ident or {}
    return (ident.get("phoneNumber") or {}).get("value") or (ident.get("rawId") or "").replace("4:", "")


@router.post("/telephony/acs/incoming")
async def acs_incoming(req: Request):
    """Event Grid subscription on the ACS resource (event type Microsoft.Communication.IncomingCall).
    Endpoint: https://<host>/telephony/acs/incoming?k=<key shown on the Phone Numbers page>"""
    events = await req.json()
    events = events if isinstance(events, list) else [events]
    for ev in events:
        etype = ev.get("eventType") or ev.get("type") or ""
        data = ev.get("data") or {}
        if etype == "Microsoft.EventGrid.SubscriptionValidationEvent":
            if not req.query_params.get("k"):
                raise HTTPException(403, "missing key")
            return JSONResponse({"validationResponse": data.get("validationCode")})
        if etype != "Microsoft.Communication.IncomingCall":
            continue
        to, frm = _acs_number(data.get("to")), _acs_number(data.get("from"))
        log.info("ACS incoming call from %s to %s", frm, to)
        num, agent, msg = await _inbound_checks("acs", _digits(to), req.query_params.get("k"), allow_platform=True)
        if msg:
            log.warning("ACS call to %s rejected: %s", to, msg)
            try:
                tid = num["tenant_id"] if num else None
                client = await providers.acs_for(tid) if tid else providers.acs_client(settings.ACS_CONNECTION_STRING)
                await client.reject_call(data["incomingCallContext"])
            except Exception as e:
                log.warning("ACS reject failed: %s", e)
            continue
        call = await _new_inbound("acs", num, agent, frm, None)
        voicelive.start_prep(call["id"], agent, "acs", call)
        try:
            conn_id = await providers.acs_answer(num["tenant_id"], data["incomingCallContext"], call["id"])
            call["provider_call_id"] = conn_id
            hot.db_update_bg(call["id"], {"provider_call_id": conn_id})
        except Exception as e:
            log.exception("[%s] ACS answer failed", call["id"][:8])
            hot.db_update_bg(call["id"], {"status": "failed", "error": str(e)[:500], "ended_at": db.now(),
                                          "ended_reason": "error"})
            await bus.live_remove(num["tenant_id"], call["id"])
    return {"ok": True}


@router.post("/telephony/acs/events")
async def acs_events(req: Request):
    """Call Automation callbacks (CloudEvents) for one call."""
    call_id = _require_sig(dict(req.query_params))
    events = await req.json()
    for ev in events if isinstance(events, list) else [events]:
        etype = (ev.get("type") or ev.get("eventType") or "").replace("Microsoft.Communication.", "")
        data = ev.get("data") or {}
        info = data.get("resultInformation") or {}
        log.info("[%s] ACS %s %s", call_id[:8], etype, info.get("message", "")[:120])
        if etype == "CallConnected":
            await bus.kv_set(f"acs_connected:{call_id}", "1", 4 * 3600)
            call = await hot.get_call(call_id)
            if call and call.get("direction") == "outbound":
                hot.db_update_bg(call_id, {"status": "in-progress", "answered_at": db.now(),
                                           "provider_call_id": data.get("callConnectionId")})
                agent = await hot.get_agent(call["agent_id"])
                c = {**call, "provider_call_id": data.get("callConnectionId")}
                hot.CALLS[call_id] = c
                if agent and call_id not in voicelive.PREP and call_id not in voicelive.LOCAL:
                    voicelive.start_prep(call_id, agent, "acs", c)
        elif etype == "CallDisconnected":
            await signal_end(call_id, force=True)
        elif etype == "CreateCallFailed":
            sub = info.get("subCode")
            status = "busy" if sub in (8571, 487) else "no-answer" if sub in (8565, 10004, 408) else "failed"
            hot.db_update_bg(call_id, {"status": status, "ended_at": db.now(), "ended_reason": status.replace("-", "_"),
                                       "error": str(info.get("message") or "")[:400]})
            call = await hot.get_call(call_id)
            if call:
                await bus.live_remove(call["tenant_id"], call_id)
        elif etype == "AddParticipantSucceeded":
            await bus.control(call_id, "participant_joined")
        elif etype in ("AddParticipantFailed", "CallTransferFailed"):
            await bus.control(call_id, "participant_failed")
        elif etype == "ContinuousDtmfRecognitionToneReceived":
            await bus.control(call_id, "dtmf", digit=str(data.get("tone") or "")[:1])
    return {"ok": True}


# ---------------- media WebSockets ----------------

async def _media(ws: WebSocket, provider: str, call_id: str, sig: str | None):
    await ws.accept()
    if provider not in ("vobiz", "twilio", "acs") or not check_sig(call_id, sig):
        log.warning("[%s] media websocket rejected (bad signature)", call_id[:8])
        await ws.close(code=4403)
        return
    log.info("[%s] media websocket accepted (%s)", call_id[:8], provider)
    call = await hot.get_call(call_id)
    agent = call and await hot.get_agent(call["agent_id"])
    if not call or not agent:
        await ws.close()
        return
    await run_bridge(ws, provider, agent, call, voicelive.take_prep(call_id))


@router.websocket("/media/{provider}/{call_id}/{sig}")
async def media(ws: WebSocket, provider: str, call_id: str, sig: str):
    await _media(ws, provider, call_id, sig)


@router.websocket("/media/{provider}/{call_id}")
async def media_legacy(ws: WebSocket, provider: str, call_id: str):
    await _media(ws, provider, call_id, None)


@router.websocket("/ws/test/{agent_id}")
async def test_call(ws: WebSocket, agent_id: str, token: str = "", draft: int = 1, vars: str = ""):
    await ws.accept()
    user = await asyncio.to_thread(user_from_token, token)
    agent = user and await hot.get_agent(agent_id, draft=bool(draft))
    if not agent or agent["tenant_id"] != user["tenant_id"]:
        await ws.close(code=4401)
        return
    try:
        variables = clean_vars(json.loads(vars)) if vars else {}
    except Exception:
        variables = {}
    call = {"id": db.new_id(), "tenant_id": user["tenant_id"], "agent_id": agent_id, "provider": "web",
            "direction": "test", "from_number": "Browser", "to_number": agent["name"], "status": "in-progress",
            "started_at": db.now(), "variables_json": json.dumps(variables), "agent_version": agent.get("version")}
    hot.insert_call_bg(call)
    await bus.live_add(user["tenant_id"], call["id"], {"direction": "test", "from": "Browser", "to": agent["name"],
                                                        "agent": agent["name"], "status": "in-progress",
                                                        "provider": "web"})
    await run_bridge(ws, "web", agent, call)
