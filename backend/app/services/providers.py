"""Telephony actions for Vobiz, Twilio and Azure Communication Services (ACS Call Automation).

ACS covers both ACS phone numbers and Direct Routing (a client's own SIP trunk through a certified SBC): to the
code they are the same - an IncomingCall event, answer with bidirectional media streaming, call-automation
actions for transfer / hang-up."""
import asyncio
import logging
import time
from xml.sax.saxutils import escape

import httpx

from .. import db
from ..config import settings
from ..security import decrypt, sign
from . import bus, hot

log = logging.getLogger("providers")


# ---------------- URLs we hand to providers (all signed) ----------------

def cb(path: str, call_id: str) -> str:
    return f"{settings.PUBLIC_BASE_URL}{path}?call_id={call_id}&sig={sign(call_id)}"


def media_url(provider: str, call_id: str) -> str:
    # Twilio drops query strings on <Stream url>, so the signature is part of the path
    return f"{settings.WS_BASE}/media/{provider}/{call_id}/{sign(call_id)}"


# ---------------- credentials ----------------

def credentials(tenant_id: str, provider: str) -> dict:
    row = db.one("SELECT * FROM provider_credentials WHERE tenant_id = ? AND provider = ?", tenant_id, provider)
    if row and (row.get("account_id") or row.get("auth_token_enc")):
        return {"id": row.get("account_id") or "", "token": decrypt(row["auth_token_enc"]), "own": True}
    if provider == "vobiz":
        return {"id": settings.VOBIZ_AUTH_ID, "token": settings.VOBIZ_AUTH_TOKEN, "own": False}
    if provider == "acs":
        return {"id": "platform", "token": settings.ACS_CONNECTION_STRING, "own": False}
    return {"id": settings.TWILIO_ACCOUNT_SID, "token": settings.TWILIO_AUTH_TOKEN, "own": False}


async def creds(tenant_id: str, provider: str) -> dict:
    return await hot.cached(("cred", tenant_id, provider), 60, credentials, tenant_id, provider, swr=True)


# ---------------- ACS ----------------

_acs_clients: dict[str, object] = {}


def acs_client(conn_str: str):
    from azure.communication.callautomation.aio import CallAutomationClient
    c = _acs_clients.get(conn_str)
    if c is None:
        c = CallAutomationClient.from_connection_string(conn_str)
        _acs_clients[conn_str] = c
    return c


async def acs_for(tenant_id: str):
    c = await creds(tenant_id, "acs")
    if not c["token"]:
        raise RuntimeError("Azure Communication Services is not configured (Settings > Phone Numbers > ACS)")
    return acs_client(c["token"])


def acs_media_options(call_id: str):
    import azure.communication.callautomation as ca
    transport = getattr(ca, "StreamingTransportType", None) or getattr(ca, "MediaStreamingTransportType")
    kw = dict(transport_url=media_url("acs", call_id), transport_type=transport.WEBSOCKET,
              content_type=ca.MediaStreamingContentType.AUDIO,
              audio_channel_type=ca.MediaStreamingAudioChannelType.MIXED, start_media_streaming=True,
              enable_bidirectional=True, audio_format=ca.AudioFormat.PCM24_K_MONO, enable_dtmf_tones=True)
    try:
        return ca.MediaStreamingOptions(**kw)
    except TypeError:                # SDK without DTMF-in-stream support
        kw.pop("enable_dtmf_tones")
        return ca.MediaStreamingOptions(**kw)


def phone_id(number: str):
    from azure.communication.callautomation import PhoneNumberIdentifier
    return PhoneNumberIdentifier(number)


async def acs_answer(tenant_id: str, incoming_call_context: str, call_id: str) -> str:
    client = await acs_for(tenant_id)
    res = await client.answer_call(incoming_call_context=incoming_call_context,
                                   callback_url=cb("/telephony/acs/events", call_id),
                                   media_streaming=acs_media_options(call_id), operation_context=call_id)
    return res.call_connection_id


async def _acs_ring_guard(tenant_id: str, call_id: str, conn_id: str, seconds: int):
    await asyncio.sleep(seconds)
    if await bus.kv_get(f"acs_connected:{call_id}", as_json=False):
        return
    log.info("[%s] no answer after %ss - cancelling", call_id[:8], seconds)
    try:
        conn = (await acs_for(tenant_id)).get_call_connection(conn_id)
        await conn.hang_up(is_for_everyone=True)
    except Exception as e:
        log.warning("[%s] ACS cancel failed: %s", call_id[:8], e)
    hot.db_update_bg(call_id, {"status": "no-answer", "ended_at": db.now(), "ended_reason": "no_answer"})
    await bus.live_remove(tenant_id, call_id)


# ---------------- place / hang up ----------------

async def place_call(tenant_id: str, provider: str, from_number: str, to_number: str, call_id: str,
                     agent: dict | None = None) -> str:
    agent = agent or {}
    ring = int(agent.get("ring_seconds") or 30)
    vm = bool(agent.get("voicemail_detection"))
    c = await asyncio.to_thread(credentials, tenant_id, provider)
    if not c["token"]:
        raise RuntimeError(f"{provider} credentials are not configured (Phone Numbers > Credentials)")
    t0 = time.time()

    if provider == "acs":
        client = acs_client(c["token"])
        res = await client.create_call(target_participant=phone_id(to_number),
                                       callback_url=cb("/telephony/acs/events", call_id),
                                       source_caller_id_number=phone_id(from_number),
                                       media_streaming=acs_media_options(call_id), operation_context=call_id)
        conn_id = res.call_connection_id
        log.info("ACS create_call accepted in %.2fs", time.time() - t0)
        asyncio.create_task(_acs_ring_guard(tenant_id, call_id, conn_id, ring))
        return conn_id

    async with httpx.AsyncClient(timeout=20) as h:
        if provider == "vobiz":
            body = {"from": from_number, "to": to_number, "answer_url": cb("/telephony/vobiz/answer", call_id),
                    "answer_method": "POST", "hangup_url": cb("/telephony/vobiz/status", call_id),
                    "hangup_method": "POST", "hangup_on_ring": ring,
                    "time_limit": int(agent.get("max_call_seconds") or 600) + 60}
            if vm:
                body.update(machine_detection="true", machine_detection_url=cb("/telephony/vobiz/amd", call_id),
                            machine_detection_method="POST")
            r = await h.post(f"{settings.VOBIZ_API_BASE}/Account/{c['id']}/Call/",
                             headers={"X-Auth-ID": c["id"], "X-Auth-Token": c["token"]}, json=body)
            log.info("Vobiz call API answered in %.2fs: HTTP %s %s", time.time() - t0, r.status_code, r.text[:300])
            r.raise_for_status()
            j = r.json()
            return str(j.get("request_uuid") or j.get("call_uuid") or j.get("CallUUID") or "")
        data = {"From": from_number, "To": to_number, "Url": cb("/telephony/twilio/answer", call_id),
                "Method": "POST", "StatusCallback": cb("/telephony/twilio/status", call_id),
                "StatusCallbackEvent": "completed", "Timeout": str(ring)}
        if vm:
            data.update(MachineDetection="DetectMessageEnd" if agent.get("voicemail_action") == "leave_message"
                        else "Enable", AsyncAmd="true", AsyncAmdStatusCallback=cb("/telephony/twilio/amd", call_id),
                        AsyncAmdStatusCallbackMethod="POST")
        r = await h.post(f"https://api.twilio.com/2010-04-01/Accounts/{c['id']}/Calls.json",
                         auth=(c["id"], c["token"]), data=data)
        log.info("Twilio call API answered in %.2fs: HTTP %s", time.time() - t0, r.status_code)
        r.raise_for_status()
        return r.json().get("sid", "")


async def hangup(tenant_id: str, provider: str, provider_call_id: str | None):
    """Force the provider to end the call so the line is free for the next caller."""
    if not provider_call_id:
        return
    try:
        c = await creds(tenant_id, provider)
    except Exception:
        return
    if not c["token"]:
        return
    if provider == "acs":
        try:
            await acs_client(c["token"]).get_call_connection(provider_call_id).hang_up(is_for_everyone=True)
        except Exception as e:
            log.info("ACS hang up: %s", e)
        return
    async with httpx.AsyncClient(timeout=10) as h:
        if provider == "vobiz":
            r = await h.delete(f"{settings.VOBIZ_API_BASE}/Account/{c['id']}/Call/{provider_call_id}/",
                               headers={"X-Auth-ID": c["id"], "X-Auth-Token": c["token"]})
            log.info("Vobiz hangup API: HTTP %s", r.status_code)
        elif provider == "twilio":
            await h.post(f"https://api.twilio.com/2010-04-01/Accounts/{c['id']}/Calls/{provider_call_id}.json",
                         auth=(c["id"], c["token"]), data={"Status": "completed"})


# ---------------- transfer ----------------
# cold: the caller is connected straight to the number.
# warm: Twilio / Vobiz play a short spoken briefing to the human before connecting (whisper);
#       ACS adds the human to the call, the AI introduces the caller to both, then the AI leaves.

async def transfer(call: dict, number: str, mode: str, summary: str, ring: int) -> str:
    """Starts the hand-off. Returns 'handoff' (the provider takes over the call), 'warm-pending' (ACS: wait for
    the participant event) or raises."""
    provider, cid, pid = call["provider"], call["id"], call.get("provider_call_id")
    await bus.kv_set(f"transfer:{cid}", {"number": number, "mode": mode, "summary": summary[:600], "ring": ring,
                                         "caller_id": call.get("to_number") if call.get("direction") == "inbound"
                                         else call.get("from_number")}, 3600)
    if provider == "twilio":
        return "handoff"           # closing the media stream makes Twilio fetch <Connect action> -> <Dial>
    c = await creds(call["tenant_id"], provider)
    if provider == "vobiz":
        if not pid:
            raise RuntimeError("Vobiz call id unknown - cannot transfer")
        async with httpx.AsyncClient(timeout=10) as h:
            r = await h.post(f"{settings.VOBIZ_API_BASE}/Account/{c['id']}/Call/{pid}/",
                             headers={"X-Auth-ID": c["id"], "X-Auth-Token": c["token"]},
                             json={"legs": "aleg", "aleg_url": cb("/telephony/vobiz/transfer", cid),
                                   "aleg_method": "POST"})
            log.info("[%s] Vobiz transfer API: HTTP %s %s", cid[:8], r.status_code, r.text[:200])
            r.raise_for_status()
        return "handoff"
    if provider == "acs":
        conn = acs_client(c["token"]).get_call_connection(pid)
        our = call.get("to_number") if call.get("direction") == "inbound" else call.get("from_number")
        if mode == "warm":
            await conn.add_participant(phone_id(number), source_caller_id_number=phone_id(our),
                                       invitation_timeout=ring, operation_context=f"warm:{cid}")
            return "warm-pending"
        try:
            await conn.transfer_call_to_participant(phone_id(number), source_caller_id_number=phone_id(our),
                                                    operation_context=f"transfer:{cid}")
        except TypeError:
            await conn.transfer_call_to_participant(phone_id(number), operation_context=f"transfer:{cid}")
        return "handoff"
    raise RuntimeError(f"Transfer is not available for {provider} calls")


async def acs_leave(call: dict):
    """Warm transfer finished: the AI leaves, the caller and the human stay connected."""
    c = await creds(call["tenant_id"], "acs")
    await acs_client(c["token"]).get_call_connection(call["provider_call_id"]).hang_up(is_for_everyone=False)


def dial_xml(provider: str, t: dict, whisper_url: str | None) -> str:
    num, ring = escape(t["number"]), int(t.get("ring") or 30)
    caller = f' callerId="{escape(t["caller_id"])}"' if t.get("caller_id") else ""
    if provider == "twilio":
        url = f' url="{escape(whisper_url)}"' if whisper_url else ""
        return f'<Dial timeout="{ring}"{caller}><Number{url}>{num}</Number></Dial>'
    confirm = f' confirmSound="{escape(whisper_url)}"' if whisper_url else ""
    return f'<Dial timeout="{ring}"{caller}{confirm}><Number>{num}</Number></Dial>'
