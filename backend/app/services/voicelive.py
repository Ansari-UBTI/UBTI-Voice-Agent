"""Bridge between a telephony / browser WebSocket and Azure Voice Live (gpt-realtime).

One CallSession per call. It streams audio both ways and adds the call-handling features on top of the
realtime model: greeting / who speaks first, barge-in, silence reminders and end-on-silence, voicemail and IVR
detection, keypad (DTMF) input, transfers (cold / warm), custom functions, knowledge search, dynamic variables,
two-channel recording, live monitoring and cost tracking."""
import asyncio
import base64
import json
import logging
import os
import re
import time

from fastapi import WebSocket
from websockets.asyncio.client import connect as ws_connect

from .. import db
from ..config import settings
from . import bus, hot, kb, postcall, pricing, providers, storage
from .aio import spawn
from .audio import audioop
from .recorder import Recorder
from .templating import fill, system_vars
from .tools import load_tools, run_tool, to_schema
from .versions import jload

log = logging.getLogger("voicelive")

# Optional language hint for the transcript, e.g. "en-US". Leave empty unless you need it - a value that Voice
# Live does not accept makes it reject the whole session configuration (the bridge then retries with less).
TRANSCRIBE_LANGS = os.getenv("TRANSCRIBE_LANGUAGES", "").strip()

PREP: dict[str, asyncio.Task] = {}         # call_id -> pre-opened Voice Live session (this replica)
LOCAL: dict[str, "CallSession"] = {}       # call_id -> running session (this replica)


async def signal_end(call_id: str, reason: str = "caller_hangup", force: bool = False):
    """Provider says the call is over (from any replica)."""
    await bus.kv_set(f"ended:{call_id}", reason, 600)
    await bus.control(call_id, "hangup", reason=reason, force=force)


def active_count() -> int:
    return len(LOCAL)


# ---------------- Provider adapters ----------------

class Adapter:
    fmt = "pcm24"            # what Voice Live is told to expect from / send to this provider
    auto_start = False       # browser: no 'start' event, greet as soon as connected
    bytes_per_sec = 48000    # outbound audio bytes per second in the provider's format
    rec_rate = 16000

    def __init__(self):
        self.stream_id = None

    def parse(self, m: dict):
        return None, None

    def from_vl(self, b64: str) -> bytes:
        return base64.b64decode(b64)

    def caller_pcm(self, b64: str) -> tuple[bytes, int]:
        return base64.b64decode(b64), 24000

    def agent_pcm(self, chunk: bytes) -> tuple[bytes, int]:
        return chunk, 24000

    def pack(self, chunk: bytes) -> dict:
        raise NotImplementedError

    def clear(self) -> dict | None:
        return None

    def reset(self):
        pass


class VobizAdapter(Adapter):
    fmt = "pcm16"            # Vobiz streams 16 kHz linear PCM (audio/x-l16)
    bytes_per_sec = 32000

    def __init__(self):
        super().__init__()
        self._rs = None

    def parse(self, m):
        ev = (m.get("event") or "").lower()
        if ev == "start":
            st = m.get("start") or {}
            self.stream_id = st.get("streamId") or m.get("streamId")
            return "start", st.get("callId") or m.get("callId")
        if ev == "media":
            self.stream_id = self.stream_id or m.get("streamId")
            return "audio", (m.get("media") or {}).get("payload") or m.get("payload")
        if ev == "dtmf":                      # not documented by Vobiz today - handled if they add it
            return "dtmf", (m.get("dtmf") or {}).get("digit") or m.get("digit")
        if ev in ("stop", "hangup", "closed"):
            return "stop", None
        return None, None

    def from_vl(self, b64):
        pcm16k, self._rs = audioop.ratecv(base64.b64decode(b64), 2, 1, 24000, 16000, self._rs)
        return pcm16k

    def caller_pcm(self, b64):
        return base64.b64decode(b64), 16000

    def agent_pcm(self, chunk):
        return chunk, 16000

    def pack(self, chunk):
        return {"event": "playAudio", "streamId": self.stream_id,
                "media": {"contentType": "audio/x-l16", "sampleRate": 16000,
                          "payload": base64.b64encode(chunk).decode()}}

    def clear(self):
        return {"event": "clearAudio", "streamId": self.stream_id}

    def reset(self):
        self._rs = None


class TwilioAdapter(Adapter):
    fmt = "ulaw"
    bytes_per_sec = 8000
    rec_rate = 8000

    def parse(self, m):
        ev = m.get("event")
        if ev == "start":
            self.stream_id = m["start"]["streamSid"]
            return "start", m["start"].get("callSid")
        if ev == "media":
            return "audio", m["media"]["payload"]
        if ev == "dtmf":
            return "dtmf", (m.get("dtmf") or {}).get("digit")
        if ev == "stop":
            return "stop", None
        return None, None

    def caller_pcm(self, b64):
        return audioop.ulaw2lin(base64.b64decode(b64), 2), 8000

    def agent_pcm(self, chunk):
        return audioop.ulaw2lin(chunk, 2), 8000

    def pack(self, chunk):
        return {"event": "media", "streamSid": self.stream_id,
                "media": {"payload": base64.b64encode(chunk).decode()}}

    def clear(self):
        return {"event": "clear", "streamSid": self.stream_id}


class AcsAdapter(Adapter):
    """Azure Communication Services bidirectional media streaming (PCM 24 kHz mono)."""
    fmt = "pcm24"

    def parse(self, m):
        kind = (m.get("kind") or m.get("Kind") or "").lower()
        if kind == "audiometadata":
            return "start", None
        if kind == "audiodata":
            d = m.get("audioData") or m.get("AudioData") or {}
            data = d.get("data") or d.get("Data")
            return ("audio", data) if data else (None, None)
        if kind == "dtmfdata":
            d = m.get("dtmfData") or m.get("DtmfData") or {}
            return "dtmf", d.get("data") or d.get("Data")
        return None, None

    def pack(self, chunk):
        return {"Kind": "AudioData", "AudioData": {"Data": base64.b64encode(chunk).decode()}, "StopAudio": None}

    def clear(self):
        return {"Kind": "StopAudio", "AudioData": None, "StopAudio": {}}


class WebAdapter(Adapter):
    fmt = "pcm24"
    auto_start = True

    def parse(self, m):
        t = m.get("type")
        if t == "audio":
            return "audio", m["data"]
        if t == "dtmf":
            return "dtmf", m.get("digit")
        if t == "stop":
            return "stop", None
        return None, None

    def pack(self, chunk):
        return {"type": "audio", "data": base64.b64encode(chunk).decode()}

    def clear(self):
        return {"type": "clear"}


ADAPTERS = {"vobiz": VobizAdapter, "twilio": TwilioAdapter, "acs": AcsAdapter, "web": WebAdapter}


def _rms(pcm: bytes) -> int:
    try:
        return audioop.rms(pcm, 2)
    except Exception:
        return -1


# ---------------- Session config ----------------

HUMAN_STYLE = """
# How you speak on the phone (very important)
- This is a LIVE PHONE CALL. Talk like a friendly, experienced human executive - relaxed, warm, confident.
- Keep replies SHORT: usually one sentence, at most two. Never give long explanations unless asked.
- Ask only ONE question at a time, then stop and wait.
- First acknowledge briefly ("Sure", "Got it", "Okay, one second", "Ah, I see"), then answer.
- Vary your words - never repeat the same phrase or greeting twice in a call.
- Use simple everyday words. No lists, no bullet points, no markdown, no emojis, no "As an AI".
- Say numbers the way people speak: phone numbers digit by digit in groups, prices like "two thousand rupees".
- If you didn't hear clearly, ask casually: "Sorry, I didn't catch that - could you say it again?"
- If you don't know something, say so honestly and offer what you CAN do. Never make up facts.
- When the caller is done, close warmly in one line and say goodbye.
"""


def _lang_rule(agent):
    lang = agent.get("language") or "auto"
    if lang == "auto" or agent.get("auto_detect_language"):
        return ("# Language (strict)\n"
                "- Start the call in English unless the agent instructions say otherwise.\n"
                "- Reply in the language the caller is CURRENTLY speaking. If the caller speaks English, reply ONLY in English.\n"
                "- Switch language only when the caller speaks a full sentence in another language "
                "(Arabic, Hindi, Urdu, Tamil, Kannada, Telugu, Malayalam...) or asks you to switch.\n"
                "- Do NOT switch because of a single word, a name, a place name, or a greeting.\n"
                "- Mixed speech like Tanglish/Hinglish: reply in the same mixed style, mostly in the language they use most.")
    return (f"# Language (strict)\n- Speak in {lang}. Switch only if the caller clearly cannot follow "
            "or explicitly asks for another language.")


def build_voice(agent, level: int = 0):
    vtype = agent.get("voice_type") or "azure-standard"
    if vtype == "azure-personal" and agent.get("speaker_profile_id"):
        return {"type": "azure-personal", "name": "DragonLatestNeural",
                "speaker_profile_id": agent["speaker_profile_id"]}
    if vtype == "openai":
        return {"type": "openai", "name": agent.get("voice_name") or "marin"}
    v = {"type": "azure-standard", "name": agent.get("voice_name") or "en-IN-AartiNeural"}
    rate = agent.get("voice_rate")
    if level < 2 and rate and float(rate) != 1.0:
        v["rate"] = str(rate)
    return v


def keywords(agent) -> list[str]:
    return [k.strip() for k in re.split(r"[,\n]", agent.get("boosted_keywords") or "") if k.strip()][:100]


def tool_list(agent: dict, custom_tools: list, kb_index, direction: str) -> list[dict]:
    tools = [to_schema(t) for t in custom_tools]
    if agent.get("end_call_enabled", True):
        tools.append({"type": "function", "name": "end_call",
                      "description": "Hang up ONLY after the caller has said goodbye or clearly has nothing more, "
                                     "and you have already said your goodbye.",
                      "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}}})
    if agent.get("transfer_number"):
        tools.append({"type": "function", "name": "transfer_call",
                      "description": "Transfer the caller to a human when they ask for a person or you cannot help.",
                      "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}}})
    if direction == "outbound" and agent.get("voicemail_detection"):
        tools.append({"type": "function", "name": "voicemail_detected",
                      "description": "Call IMMEDIATELY when you hear a voicemail greeting or answering machine "
                                     "(e.g. 'leave a message after the tone', 'the person you are calling is not "
                                     "available', 'please record your message'). Do not speak to the machine first.",
                      "parameters": {"type": "object", "properties": {}}})
    if direction == "outbound" and agent.get("ivr_hangup"):
        tools.append({"type": "function", "name": "ivr_detected",
                      "description": "Call when you reach an automated phone menu / IVR instead of a person "
                                     "(e.g. 'press 1 for sales', 'for English press 2').",
                      "parameters": {"type": "object", "properties": {}}})
    if kb_index is not None and not kb_index.inline and kb_index.chunks:
        tools.append({"type": "function", "name": "search_knowledge",
                      "description": "Search the company knowledge base. Use it for ANY question about products, "
                                     "prices, policies, locations, hours or other company facts before answering.",
                      "parameters": {"type": "object", "properties": {
                          "query": {"type": "string", "description": "What to look up, in English, a few keywords"}},
                          "required": ["query"]}})
    return tools


def build_instructions(agent: dict, ctx: dict, kb_index, tools: list[dict], direction: str) -> str:
    parts = [fill(agent.get("system_prompt") or "You are a helpful phone assistant.", ctx), HUMAN_STYLE,
             _lang_rule(agent),
             f"# Call context\n- Today is {ctx.get('day_of_week')}, {ctx.get('current_date')}, "
             f"time {ctx.get('current_time')}.\n- This is an {direction} call."]
    if direction == "outbound":
        parts.append("- You placed this call. If someone answers with 'hello', greet them and say why you are calling.")
    screening = fill(agent.get("screening_message") or "", ctx).strip()
    if screening:
        parts.append("# Call screening\n- If an automated call screener (iPhone / Android / Google Assistant) asks "
                     f"who is calling and why, answer exactly: \"{screening}\" and then wait.")
    names = {t["name"] for t in tools}
    if names:
        rules = ["# Tools", "- Use a tool when you need live data or to take an action; never guess that data.",
                 "- Collect all required details first, confirm them in one short sentence, then call the tool.",
                 "- Explain the result in simple spoken words, not raw data."]
        if "end_call" in names:
            rules.append("- Say goodbye BEFORE end_call.")
        if "transfer_call" in names:
            rules.append("- Tell the caller you are connecting them before transfer_call.")
        if "voicemail_detected" in names:
            rules.append("- If you reach a voicemail or answering machine, call voicemail_detected right away.")
        if "ivr_detected" in names:
            rules.append("- If you reach an automated phone menu, call ivr_detected right away.")
        if "search_knowledge" in names:
            rules.append("- For company facts, call search_knowledge first and answer only from what it returns.")
        parts.append("\n".join(rules))
    if kb_index is not None and kb_index.docs:
        if kb_index.inline:
            parts.append("# Knowledge base (use only when relevant; never read it out word for word)\n"
                         + kb_index.inline_text())
        else:
            parts.append(f"# Knowledge base\nThe knowledge base covers: {kb_index.titles()}. "
                         "Use search_knowledge to look things up; never read results out word for word.")
    return "\n\n".join(parts)


def build_session(agent: dict, fmt: str, instructions: str, tools: list[dict], level: int = 0) -> dict:
    """level 0 = full settings.
    level 1 = drops settings that are not in Microsoft's published reference (used if Voice Live rejects level 0).
    level 2 = bare minimum - only the fields in Microsoft's own session.update example."""
    sens = (agent.get("interrupt_sensitivity") or "medium").lower()
    phone = fmt != "pcm24" or agent.get("_provider") == "acs"
    thr = ({"low": 0.7, "medium": 0.55, "high": 0.4} if phone else {"low": 0.6, "medium": 0.4, "high": 0.25}).get(sens, 0.5)
    auto = (agent.get("language") or "auto") == "auto" or agent.get("auto_detect_language")
    td = {"type": "azure_semantic_vad", "threshold": thr, "prefix_padding_ms": 300,
          "silence_duration_ms": 550 if phone else 400}
    if level == 0:
        if auto:
            td["type"] = "azure_semantic_vad_multilingual"
        td.update(remove_filler_words=False, create_response=True,
                  interrupt_response=False)   # the bridge confirms real barge-in itself
    s = {
        "modalities": ["text", "audio"],
        "instructions": instructions,
        "temperature": max(0.6, float(agent.get("temperature") or 0.8)),
        "voice": build_voice(agent, level),
        "turn_detection": td,
    }
    if level <= 1:
        s["input_audio_noise_reduction"] = {"type": "azure_deep_noise_suppression"}
        s["input_audio_echo_cancellation"] = {"type": "server_echo_cancellation"}
        s["input_audio_transcription"] = {"model": "azure-speech"}
        if level == 0:
            if not auto:
                s["input_audio_transcription"]["language"] = agent.get("language")
            elif TRANSCRIBE_LANGS:
                s["input_audio_transcription"]["language"] = TRANSCRIBE_LANGS
            kw = keywords(agent)
            if kw:
                s["input_audio_transcription"]["phrase_list"] = kw
    if tools:
        s["tools"] = tools
        s["tool_choice"] = "auto"
    if fmt == "ulaw":
        s["input_audio_format"] = "g711_ulaw"
        s["output_audio_format"] = "g711_ulaw"
    elif fmt == "pcm16":
        s["input_audio_sampling_rate"] = 16000
    else:
        s["input_audio_sampling_rate"] = 24000
    return s


async def connect_voicelive(model: str | None):
    if not settings.VL_ENDPOINT or not settings.VL_KEY:
        raise RuntimeError("AZURE_VOICELIVE_ENDPOINT / AZURE_VOICELIVE_API_KEY not configured")
    base = settings.VL_ENDPOINT.replace("https://", "wss://").replace("http://", "ws://")
    url = f"{base}/voice-live/realtime?api-version={settings.VL_API_VERSION}&model={model or settings.VL_MODEL}"
    return await ws_connect(url, additional_headers={"api-key": settings.VL_KEY},
                            max_size=None, ping_interval=20, open_timeout=15)


# ---------------- Pre-opened sessions ----------------

class Prepared:
    def __init__(self, vl, kb_index, custom_tools, ctx, instructions, tools):
        self.vl, self.kb_index, self.custom_tools, self.ctx = vl, kb_index, custom_tools, ctx
        self.instructions, self.tools = instructions, tools


async def call_context(agent: dict, call: dict) -> dict:
    t = await hot.tenant(call["tenant_id"])
    ctx = system_vars(call, (t or {}).get("timezone"), agent.get("name") or "")
    ctx.update(jload(agent.get("variables_json"), {}))              # agent defaults
    ctx.update(jload(call.get("variables_json"), {}))               # per call (API / batch row)
    return ctx


async def prepare(agent: dict, fmt: str, call: dict) -> Prepared:
    """Load knowledge/tools, open + configure the Voice Live session."""
    t0 = time.time()

    async def kb_():
        try:
            return await kb.index_for(agent)
        except Exception as e:
            log.warning("knowledge base not loaded for agent %s: %s", agent["id"][:8], e)
            return None

    async def tools_():
        if agent.get("tools") is not None:                          # published snapshot
            return [{**t, "params": t.get("params") or jload(t.get("parameters_json"), []),
                     "headers": t.get("headers") or jload(t.get("headers_json"), {})} for t in agent["tools"]]
        try:
            return await hot.cached(("tools", agent["id"]), 20, load_tools, agent["id"], timeout=4.0)
        except Exception:
            return []

    index, ctools, ctx, vl = await asyncio.gather(kb_(), tools_(), call_context(agent, call),
                                                  connect_voicelive(agent.get("model")))
    direction = call.get("direction") or "inbound"
    tools = tool_list(agent, ctools, index, direction)
    instructions = build_instructions(agent, ctx, index, tools, direction)
    await vl.send(json.dumps({"type": "session.update", "session": build_session(agent, fmt, instructions, tools)}))
    log.info("Voice Live session prepared in %.1fs (knowledge %s, %d custom tools)", time.time() - t0,
             "none" if not index or not index.docs else ("inline" if index.inline else f"{len(index.chunks)} chunks"),
             len(ctools))
    return Prepared(vl, index, ctools, ctx, instructions, tools)


def start_prep(call_id: str, agent: dict, provider: str, call: dict):
    """Called from the answer webhook so Voice Live is ready before the phone provider opens its stream."""
    a = {**agent, "_provider": provider}
    PREP[call_id] = asyncio.create_task(prepare(a, ADAPTERS[provider].fmt, call))
    asyncio.get_running_loop().call_later(45, reap_unclaimed, call_id, call.get("tenant_id"))


def take_prep(call_id: str):
    return PREP.pop(call_id, None)


def _close_task(t: asyncio.Task):
    def _done(task):
        try:
            asyncio.ensure_future(task.result().vl.close())
        except BaseException:
            pass
    if t.done():
        _done(t)
    else:
        t.add_done_callback(_done)


def reap_unclaimed(call_id: str, tenant_id: str | None):
    t = PREP.pop(call_id, None)
    if t is None:
        return  # the media stream connected and took it
    if call_id in LOCAL:            # the bridge started without it (opened its own session) - just close this one
        _close_task(t)
        return
    log.warning("[%s] the phone provider never opened the media stream (check PUBLIC_BASE_URL and the answer URL) "
                "- closing", call_id[:8])
    _close_task(t)
    if tenant_id:
        spawn(bus.live_remove(tenant_id, call_id))
    hot.db_update_bg(call_id, {"status": "failed", "ended_at": db.now(), "ended_reason": "no_media",
                               "error": "Media stream never connected - check PUBLIC_BASE_URL / answer URL"})


# ---------------- Bridge ----------------

FRAME_MS = 100
BARGE_IN_MS = {"low": 700, "medium": 450, "high": 250}
REPLY_WAIT_S = 2.5       # if Voice Live has not started a reply this long after the caller stopped, ask for one
MIN_TURN_S = 0.7         # ...but only for caller turns at least this long (ignores line noise)
IGNORE_EARLY_HANGUP_S = 3


class CallSession:
    def __init__(self, ws: WebSocket, provider: str, agent: dict, call: dict, prep: asyncio.Task | None = None):
        self.ws, self.provider, self.call, self.prep_task = ws, provider, call, prep
        self.agent = {**agent, "_provider": provider}
        self.ad = ADAPTERS[provider]()
        self.id = call["id"]
        self.tag = self.id[:8]
        self.direction = call.get("direction") or "inbound"
        now = time.time()
        self.t0 = now
        self.out = bytearray()
        self.frame = int(self.ad.bytes_per_sec * FRAME_MS / 1000)
        self.frame -= self.frame % 2
        self.barge_ms = BARGE_IN_MS.get((agent.get("interrupt_sensitivity") or "medium").lower(), 450)
        self.done = asyncio.Event()
        self.session_ready = asyncio.Event()
        self.send_lock = asyncio.Lock()
        self.usage = pricing.empty_usage()
        self.rec: Recorder | None = None
        self.vl = None
        self.p: Prepared | None = None
        self.tool_map: dict = {}
        self.turns: list[dict] = []
        self.dtmf_buf = ""
        self.dtmf_task: asyncio.Task | None = None
        # state
        self.greeted = False
        self.first_out = False
        self.responding = False
        self.play_until = 0.0
        self.speech_at = None           # time caller speech started (None = not speaking)
        self.speech_t0 = 0.0
        self.last_caller = now          # last time the caller spoke or pressed a key
        self.last_activity = now        # last caller speech / agent audio end / tool / reminder
        self.last_audio = now           # last media frame from the provider
        self.last_resp = 0.0
        self.tool_busy = 0
        self.reminders = 0
        self.end_at = None
        self.end_after_resp = False
        self.end_reason = None
        self.handoff = False            # the provider owns the call now - do not hang up
        self.acs_leave = False
        self.transferred = False
        self.transfer_target = None
        self.pending_transfer = None
        self.warm_wait: asyncio.Future | None = None
        self.in_voicemail = False
        self.vm_handled = False
        self.pid = call.get("provider_call_id")
        self.level = 0
        self.n = {"frames": 0, "rms_max": 0, "start": 0, "stop": 0, "resp": 0, "err": 0, "forced": 0}

    # ---------- helpers ----------

    def agent_speaking(self) -> bool:
        return self.responding or time.time() < self.play_until

    async def send_client(self, msg: dict | None):
        if msg is None:
            return
        async with self.send_lock:
            await self.ws.send_text(json.dumps(msg))

    async def vl_send(self, msg: dict):
        await self.vl.send(json.dumps(msg))

    def finish(self, reason: str, delay_after_audio: float = 1.2):
        """End the call once the agent's queued audio has played."""
        self.end_reason = self.end_reason or reason
        self.end_at = max(time.time(), self.play_until) + delay_after_audio

    async def say(self, instructions: str, tools: bool = False):
        r = {"instructions": instructions}
        if not tools:
            r["tool_choice"] = "none"
        await self.vl_send({"type": "response.create", "response": r})

    async def save_turn(self, role, text):
        text = (text or "").strip()
        if not text:
            return
        off = int((time.time() - self.t0) * 1000)
        self.turns.append({"role": role, "text": text})
        await hot.ensure_inserted(self.id)
        spawn(asyncio.to_thread(db.insert, "call_transcripts", {
            "call_id": self.id, "role": role, "text": text[:4000], "offset_ms": off, "created_at": db.now()}))
        if self.provider == "web":
            await self.send_client({"type": "transcript", "role": role, "text": text})
        spawn(bus.publish(bus.mon(self.id), {"type": "transcript", "role": role, "text": text, "offset_ms": off}))

    async def send_frames(self, final: bool = False):
        while len(self.out) >= self.frame or (final and self.out):
            chunk = bytes(self.out[:self.frame])
            del self.out[:self.frame]
            now = time.time()
            start = max(self.play_until, now)
            self.play_until = start + len(chunk) / self.ad.bytes_per_sec
            self.last_activity = self.play_until
            if self.rec:
                pcm, rate = self.ad.agent_pcm(chunk)
                self.rec.agent(pcm, rate, start)
            if not self.first_out:
                self.first_out = True
                log.info("[%s] first agent audio sent %.1fs after the stream connected", self.tag, now - self.t0)
            await self.send_client(self.ad.pack(chunk))

    async def interrupt(self, why: str = "caller interrupted"):
        self.out.clear()
        self.ad.reset()
        if self.rec:
            self.rec.cut_agent(time.time())
        self.play_until = 0.0
        if self.responding:
            await self.vl_send({"type": "response.cancel"})
        await self.send_client(self.ad.clear())
        log.info("[%s] %s", self.tag, why)

    # ---------- turn-taking ----------

    async def confirm_barge_in(self, started_at: float):
        await asyncio.sleep(self.barge_ms / 1000)
        if self.speech_at == started_at and self.agent_speaking() and not self.end_at:
            await self.interrupt()

    async def ensure_reply(self, stop_t: float, dur: float):
        """Safety net: if the caller spoke but Voice Live never started a reply, ask for one."""
        await asyncio.sleep(REPLY_WAIT_S)
        if (self.last_resp >= stop_t or self.responding or self.tool_busy or self.done.is_set()
                or dur < MIN_TURN_S or self.agent_speaking() or self.end_at or self.vm_handled):
            return
        self.n["forced"] += 1
        log.warning("[%s] Voice Live had not replied %.1fs after the caller stopped speaking - requesting a reply",
                    self.tag, REPLY_WAIT_S)
        await self.vl_send({"type": "response.create"})

    async def greet(self):
        if self.greeted:
            return
        self.greeted = True
        self.last_audio = self.last_activity = self.last_caller = time.time()
        delay = int(self.agent.get("begin_delay_ms") or 0)
        if delay:
            await asyncio.sleep(min(delay, 5000) / 1000)
        try:
            await asyncio.wait_for(self.session_ready.wait(), 5)
        except asyncio.TimeoutError:
            log.warning("[%s] session.updated not received, greeting anyway", self.tag)
        if (self.agent.get("start_speaker") or "agent") == "user":
            log.info("[%s] waiting for the caller to speak first", self.tag)
            return
        if self.speech_at or self.vm_handled:
            return                         # caller already talking (or voicemail) - let VAD drive
        msg = fill(self.agent.get("first_message") or "", self.p.ctx).strip()
        log.info("[%s] greeting requested (%.1fs after connect)", self.tag, time.time() - self.t0)
        if msg:
            await self.say(f'Say exactly this greeting warmly and naturally, then stop and wait: "{msg}"')
        else:
            await self.vl_send({"type": "response.create"})

    # ---------- DTMF ----------

    async def on_digit(self, d: str):
        if not d or not self.agent.get("dtmf_enabled"):
            return
        d = str(d).strip()[:1]
        self.last_caller = self.last_activity = time.time()
        self.reminders = 0
        term = (self.agent.get("dtmf_terminator") or "").strip()
        if term and d == term:
            return await self.flush_digits()
        self.dtmf_buf += d
        mx = int(self.agent.get("dtmf_max_digits") or 0)
        if mx and len(self.dtmf_buf) >= mx:
            return await self.flush_digits()
        if self.dtmf_task:
            self.dtmf_task.cancel()
        self.dtmf_task = asyncio.create_task(self._dtmf_timeout())

    async def _dtmf_timeout(self):
        try:
            await asyncio.sleep(max(500, int(self.agent.get("dtmf_timeout_ms") or 2500)) / 1000)
            await self.flush_digits()
        except asyncio.CancelledError:
            pass

    async def flush_digits(self):
        digits, self.dtmf_buf = self.dtmf_buf, ""
        if self.dtmf_task and self.dtmf_task is not asyncio.current_task():
            self.dtmf_task.cancel()
        self.dtmf_task = None
        if not digits:
            return
        log.info("[%s] keypad input: %s", self.tag, digits)
        await self.save_turn("dtmf", digits)
        if self.agent_speaking():
            await self.interrupt("keypad input")
        await self.vl_send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": f"[The caller entered on the keypad: {digits}]"}]}})
        await self.vl_send({"type": "response.create"})

    # ---------- voicemail / IVR ----------

    async def on_voicemail(self, source: str):
        if self.vm_handled or self.direction != "outbound":
            return
        self.vm_handled = True
        self.in_voicemail = True
        log.info("[%s] voicemail detected (%s)", self.tag, source)
        await self.save_turn("system", f"[voicemail detected by {source}]")
        if self.agent_speaking():
            await self.interrupt("voicemail - stop talking")
        if (self.agent.get("voicemail_action") or "hangup") != "leave_message" or not (
                self.agent.get("voicemail_message") or "").strip():
            self.end_reason = "voicemail"
            self.end_at = time.time()
            return
        # wait for the greeting / beep to finish: 1.5 s without speech (max 20 s)
        deadline = time.time() + 20
        quiet_since = time.time()
        while time.time() < deadline and not self.done.is_set():
            if self.speech_at:
                quiet_since = time.time()
            elif time.time() - quiet_since > 1.5:
                break
            await asyncio.sleep(0.1)
        if self.responding:                      # anything the model started for the machine
            await self.vl_send({"type": "response.cancel"})
            await self.wait_idle(2)
        msg = fill(self.agent["voicemail_message"], self.p.ctx).strip()
        self.end_reason = "voicemail"
        self.end_after_resp = True
        await self.say(f'You reached a voicemail. Say exactly this message clearly, then stop: "{msg}"')

    # ---------- tools ----------

    async def handle_custom_tool(self, name: str, call_ref: str, args: dict):
        self.tool_busy += 1
        try:
            t = self.tool_map[name]
            log.info("[%s] function call: %s %s", self.tag, name, json.dumps(args)[:200])
            if t.get("wait_message"):
                await self.say(f'Briefly tell the caller, in their language: "{t["wait_message"]}". Nothing else.')
            result = await run_tool(t, args, self.p.ctx)
            log.info("[%s] function result: %s", self.tag, result[:200])
            await self.save_turn("system", f"[{name}] {json.dumps(args)[:300]} -> {result[:300]}")
            await self.tool_output(call_ref, result)
        finally:
            self.tool_busy -= 1
            self.last_activity = time.time()

    async def tool_output(self, call_ref: str, output: str, respond: bool = True):
        await self.vl_send({"type": "conversation.item.create", "item": {
            "type": "function_call_output", "call_id": call_ref, "output": output}})
        if respond:
            for _ in range(50):
                if not self.responding:
                    break
                await asyncio.sleep(0.1)
            await self.vl_send({"type": "response.create"})

    async def search_knowledge(self, call_ref: str, args: dict):
        self.tool_busy += 1
        try:
            q = str(args.get("query") or "")
            res = await self.p.kb_index.search(q, 4) if self.p.kb_index else []
            log.info("[%s] knowledge search '%s' -> %d results", self.tag, q[:80], len(res))
            await self.save_turn("system", f"[search_knowledge] {q[:200]} -> {len(res)} results")
            await self.tool_output(call_ref, kb.format_results(res)[:6000])
        finally:
            self.tool_busy -= 1

    async def on_function(self, e: dict):
        name, ref = e.get("name"), e.get("call_id")
        try:
            args = json.loads(e.get("arguments") or "{}")
        except Exception:
            args = {}
        if name in self.tool_map:
            asyncio.create_task(self.handle_custom_tool(name, ref, args))
            return
        if name == "search_knowledge":
            asyncio.create_task(self.search_knowledge(ref, args))
            return
        await self.save_turn("system", f"[{name}] {e.get('arguments', '')}")
        if name == "voicemail_detected":
            await self.tool_output(ref, "ok", respond=False)
            asyncio.create_task(self.on_voicemail("agent"))
        elif name == "ivr_detected":
            await self.tool_output(ref, "ok", respond=False)
            if self.agent.get("ivr_hangup") and self.direction == "outbound":
                self.end_reason = "ivr"
                self.end_at = time.time()
        elif name == "transfer_call":
            asyncio.create_task(self.start_transfer(ref, args.get("reason") or ""))
        else:                                    # end_call
            await self.tool_output(ref, "ok", respond=False)
            self.finish("agent_hangup", 1.5)

    async def wait_idle(self, seconds: float = 5.0):
        for _ in range(int(seconds * 10)):
            if not self.responding:
                return
            await asyncio.sleep(0.1)

    async def start_transfer(self, ref: str, reason: str):
        """Tell the caller we are connecting them; the transfer starts when that sentence has been played."""
        await self.tool_output(ref, "Connecting now. Tell the caller in one short sentence that you are "
                                    "connecting them, then stay quiet.", respond=False)
        await self.wait_idle()
        self.pending_transfer = reason
        await self.vl_send({"type": "response.create"})

    async def do_transfer(self, reason: str):
        number = self.agent.get("transfer_number")
        mode = (self.agent.get("transfer_mode") or "cold").lower()
        if self.provider == "web":
            await self.save_turn("system", f"[transfer to {number} ({mode}) - simulated in browser test]")
            self.finish("transfer", 0.5)
            return
        summary = await postcall.quick_summary(self.turns, reason) if mode == "warm" else reason
        try:
            res = await providers.transfer({**self.call, "provider_call_id": self.pid}, number, mode, summary,
                                           int(self.agent.get("transfer_ring_seconds") or 30))
        except Exception as ex:
            log.warning("[%s] transfer failed: %s", self.tag, ex)
            await self.save_turn("system", f"[transfer failed: {str(ex)[:200]}]")
            await self.say("Apologise briefly: you could not connect them to a colleague right now. "
                           "Offer to help yourself or to arrange a call back.", tools=True)
            return
        self.transferred, self.transfer_target = True, number
        if res == "handoff":
            self.handoff = True
            self.end_reason = "transfer"
            self.end_at = time.time()
            return
        # ACS warm transfer: wait until the human joins, then introduce and leave
        self.warm_wait = asyncio.get_running_loop().create_future()
        try:
            joined = await asyncio.wait_for(self.warm_wait, int(self.agent.get("transfer_ring_seconds") or 30) + 10)
        except asyncio.TimeoutError:
            joined = False
        self.warm_wait = None
        if not joined:
            self.transferred = False
            await self.save_turn("system", "[warm transfer: nobody answered]")
            await self.say("Tell the caller your colleague is not available right now and offer to help yourself "
                           "or to arrange a call back.", tools=True)
            return
        await self.save_turn("system", f"[warm transfer: {number} joined]")
        self.handoff = self.acs_leave = True
        self.end_reason = "transfer"
        self.end_after_resp = True
        await self.say("A colleague has just joined this call. In one or two short sentences, introduce the caller "
                       f"to them using this briefing: \"{summary}\". Then say you are leaving them to it. Stop after that.")

    # ---------- loops ----------

    async def client_loop(self):
        if self.ad.auto_start:
            asyncio.create_task(self.greet())
        while True:
            fr = await self.ws.receive()
            if fr.get("type") == "websocket.disconnect":
                log.info("[%s] caller stream closed", self.tag)
                self.end_reason = self.end_reason or "caller_hangup"
                return
            raw = fr.get("text") or (fr.get("bytes") or b"").decode("utf-8", "ignore")
            if not raw:
                continue
            try:
                m = json.loads(raw)
            except Exception:
                continue
            kind, data = self.ad.parse(m)
            if kind == "audio" and data:
                self.last_audio = time.time()
                self.n["frames"] += 1
                if self.rec or self.n["frames"] % 5 == 0:
                    pcm, rate = self.ad.caller_pcm(data)
                    if self.rec:
                        self.rec.caller(pcm, rate)
                    if self.n["frames"] % 5 == 0:
                        self.n["rms_max"] = max(self.n["rms_max"], _rms(pcm))
                if self.n["frames"] in (150, 600):
                    log.info("[%s] caller audio check after %d frames: loudest level so far = %d "
                             "(under ~100 = silence / muted line, a speaking voice is usually 500+)",
                             self.tag, self.n["frames"], self.n["rms_max"])
                if not self.greeted:
                    asyncio.create_task(self.greet())
                await self.vl_send({"type": "input_audio_buffer.append", "audio": data})
            elif kind == "start":
                log.info("[%s] stream start event received", self.tag)
                if data:
                    self.pid = data
                    hot.db_update_bg(self.id, {"provider_call_id": data})
                asyncio.create_task(self.greet())
            elif kind == "dtmf":
                await self.on_digit(data)
            elif kind == "stop":
                self.end_reason = self.end_reason or "caller_hangup"
                return

    async def vl_loop(self):
        async for raw in self.vl:
            e = json.loads(raw)
            t = e.get("type", "")
            if t in ("response.audio.delta", "response.output_audio.delta"):
                if self.vm_handled and not self.end_after_resp:
                    continue
                self.out.extend(self.ad.from_vl(e["delta"]))
                await self.send_frames()
            elif t in ("response.audio.done", "response.output_audio.done"):
                await self.send_frames(final=True)
            elif t == "response.created":
                self.responding = True
                self.last_resp = time.time()
                self.n["resp"] += 1
            elif t == "response.done":
                self.responding = False
                await self.send_frames(final=True)
                pricing.add_usage(self.usage, (e.get("response") or {}).get("usage"))
                if self.pending_transfer is not None:
                    reason, self.pending_transfer = self.pending_transfer, None
                    asyncio.create_task(self._transfer_after_audio(reason))
                elif self.end_after_resp and (e.get("response") or {}).get("status") != "cancelled":
                    self.finish(self.end_reason or "agent_hangup", 1.0)
            elif t == "input_audio_buffer.speech_started":
                now = time.time()
                self.speech_at = self.speech_t0 = now
                self.last_caller = self.last_activity = now
                self.reminders = 0
                self.n["start"] += 1
                if self.n["start"] <= 3:
                    log.info("[%s] Voice Live hears the caller speaking", self.tag)
                if not self.greeted:
                    self.greeted = True               # caller spoke first
                if self.agent_speaking():
                    asyncio.create_task(self.confirm_barge_in(now))
            elif t == "input_audio_buffer.speech_stopped":
                now = time.time()
                self.speech_at = None
                self.last_caller = self.last_activity = now
                self.n["stop"] += 1
                asyncio.create_task(self.ensure_reply(now, now - (self.speech_t0 or now)))
            elif t == "session.updated":
                self.session_ready.set()
                log.info("[%s] Voice Live session ready (settings level %d)", self.tag, self.level)
            elif t == "conversation.item.input_audio_transcription.completed":
                log.info("[%s] caller said: %s", self.tag, (e.get("transcript") or "").strip()[:200])
                await self.save_turn("user", e.get("transcript", ""))
            elif t in ("response.audio_transcript.done", "response.output_audio_transcript.done"):
                log.info("[%s] agent said: %s", self.tag, (e.get("transcript") or "").strip()[:200])
                await self.save_turn("agent", e.get("transcript", ""))
            elif t == "response.function_call_arguments.done":
                await self.on_function(e)
            elif t == "error":
                err = e.get("error", {})
                if err.get("code") in ("response_cancel_not_active", "conversation_already_has_active_response"):
                    continue
                self.n["err"] += 1
                log.error("[%s] Voice Live error: %s", self.tag, err)
                await self.save_turn("system", f"[error] {err.get('message', '')}")
                if not self.session_ready.is_set():
                    # Voice Live rejected the session settings -> it keeps its own defaults (wrong audio rate etc.).
                    if self.level < 2:
                        self.level += 1
                        log.warning("[%s] retrying the session with %s settings", self.tag,
                                    "reduced" if self.level == 1 else "minimal")
                        await self.vl_send({"type": "session.update", "session": build_session(
                            self.agent, self.ad.fmt, self.p.instructions, self.p.tools, self.level)})
                    else:
                        self.session_ready.set()

    async def _transfer_after_audio(self, reason: str):
        while time.time() < self.play_until + 0.3 and not self.done.is_set():
            await asyncio.sleep(0.1)
        await self.do_transfer(reason)

    async def control_loop(self):
        async for m in bus.subscribe(bus.ctl(self.id)):
            typ = m.get("type")
            if typ == "hangup":
                if time.time() - self.t0 < IGNORE_EARLY_HANGUP_S and not m.get("force"):
                    log.info("[%s] ignoring hangup webhook %.1fs after start", self.tag, time.time() - self.t0)
                    continue
                if self.handoff:
                    continue
                self.end_reason = self.end_reason or m.get("reason") or "caller_hangup"
                self.done.set()
                return
            if typ == "end":
                self.end_reason = "operator"
                self.done.set()
                return
            if typ == "voicemail":
                asyncio.create_task(self.on_voicemail(m.get("source") or "carrier"))
            elif typ == "dtmf":
                await self.on_digit(m.get("digit"))
            elif typ in ("participant_joined", "participant_failed") and self.warm_wait and not self.warm_wait.done():
                self.warm_wait.set_result(typ == "participant_joined")

    async def watchdog(self):
        a = self.agent
        max_s = int(a.get("max_call_seconds") or 600)
        silence_end = int(a.get("silence_end_seconds") or 0)
        remind_s = int(a.get("reminder_seconds") or 0)
        remind_max = int(a.get("reminder_max") or 0)
        while True:
            await asyncio.sleep(0.5)
            now = time.time()
            if self.done.is_set():
                return
            if self.end_at and now >= max(self.end_at, self.play_until):
                return
            if not self.ad.auto_start and self.greeted and now - self.last_audio > 8:
                log.info("[%s] no audio from the caller for 8s - closing", self.tag)
                self.end_reason = self.end_reason or "caller_hangup"
                return
            if now - self.t0 > max_s:
                await self.save_turn("system", "[max duration reached]")
                self.end_reason = "max_duration"
                return
            busy = (self.speech_at is not None or self.agent_speaking() or self.tool_busy or self.end_at
                    or self.vm_handled or self.pending_transfer is not None or self.warm_wait is not None
                    or self.dtmf_buf or not self.session_ready.is_set())
            if busy:
                continue
            if silence_end and now - self.last_caller >= silence_end:
                log.info("[%s] caller silent for %ss - ending", self.tag, silence_end)
                await self.save_turn("system", f"[caller silent for {silence_end}s]")
                self.end_reason = "silence"
                self.end_after_resp = True
                self.end_at = now + 8             # safety: end even if the goodbye never comes
                await self.say("The caller has been silent for a long time. Say a short, polite goodbye in one "
                               "sentence, then stop.")
                continue
            if remind_s and self.reminders < remind_max and now - self.last_activity >= remind_s:
                self.reminders += 1
                self.last_activity = now
                log.info("[%s] caller quiet for %ss - reminder %d", self.tag, remind_s, self.reminders)
                await self.say("The caller has gone quiet. Check in naturally in a few words, e.g. ask if they are "
                               "still there or if they need a moment. Do not repeat your previous sentence.")

    # ---------- run ----------

    async def run(self):
        LOCAL[self.id] = self
        await bus.kv_set(f"bridge:{self.id}", "1", int(self.agent.get("max_call_seconds") or 600) + 600)
        ctl_task = asyncio.create_task(self.control_loop())     # subscribe before anything can be missed
        try:
            self.p = await (self.prep_task if self.prep_task is not None else prepare(self.agent, self.ad.fmt, self.call))
        except Exception as e:
            log.exception("[%s] Voice Live connect failed", self.tag)
            ctl_task.cancel()
            LOCAL.pop(self.id, None)
            hot.db_update_bg(self.id, {"status": "failed", "error": str(e)[:500], "ended_at": db.now(),
                                       "ended_reason": "error"})
            await bus.live_remove(self.call["tenant_id"], self.id)
            await bus.publish(bus.mon(self.id), {"type": "ended", "reason": "error"})
            try:
                await self.ws.close()
            except Exception:
                pass
            hot.finish(self.id)
            return
        self.vl = self.p.vl
        self.tool_map = {t["name"]: t for t in self.p.custom_tools}
        if self.agent.get("recording_enabled", True) and self.provider != "web" or self.agent.get("_record_test"):
            try:
                self.rec = Recorder(self.id, self.ad.rec_rate)
            except Exception as e:
                log.warning("[%s] recording disabled: %s", self.tag, e)
        if await bus.kv_get(f"ended:{self.id}", as_json=False):
            self.end_reason = "caller_hangup"
            self.done.set()
        log.info("[%s] %s media stream connected, Voice Live ready after %.1fs (%d active calls here)",
                 self.tag, self.provider, time.time() - self.t0, active_count())
        await bus.publish(bus.mon(self.id), {"type": "status", "status": "in-progress"})
        spawn(postcall.fire(self.call["tenant_id"], "call.started", postcall.public_call(self.call)))

        tasks = [asyncio.create_task(c) for c in (self.client_loop(), self.vl_loop(), self.watchdog())]
        tasks.append(asyncio.create_task(self.done.wait()))
        try:
            await asyncio.wait(tasks + [ctl_task], return_when=asyncio.FIRST_COMPLETED)
        except Exception:
            log.exception("bridge error")
        finally:
            for t in tasks + [ctl_task]:
                t.cancel()
            if self.dtmf_task:
                self.dtmf_task.cancel()
            spawn(self.finalize())

    async def finalize(self):
        """Runs as its own task so cleanup completes even if the request handler is cancelled."""
        call, tid = self.call, self.call["tenant_id"]
        for c in (self.vl, self.ws):
            try:
                await c.close()
            except Exception:
                pass
        if self.acs_leave:
            try:
                await providers.acs_leave({**call, "provider_call_id": self.pid})
            except Exception as ex:
                log.warning("[%s] ACS leave failed: %s", self.tag, ex)
        elif self.provider in ("vobiz", "twilio", "acs") and self.pid and not self.handoff:
            try:                                      # free the phone line first - no DB wait
                await providers.hangup(tid, self.provider, self.pid)
            except Exception as ex:
                log.warning("[%s] provider hangup failed: %s", self.tag, ex)
        dur = int(time.time() - self.t0)
        reason = self.end_reason or "caller_hangup"
        log.info("[%s] call ended after %ds (%s) | caller audio frames=%d loudest=%d | speech started/stopped=%d/%d | "
                 "replies=%d forced=%d | voice live errors=%d | settings level=%d",
                 self.tag, dur, reason, self.n["frames"], self.n["rms_max"], self.n["start"], self.n["stop"],
                 self.n["resp"], self.n["forced"], self.n["err"], self.level)
        ai, tel = pricing.call_cost(self.agent.get("model") or settings.VL_MODEL, self.usage, self.provider, dur)
        upd = {"status": "completed", "ended_at": db.now(), "duration_sec": dur, "ended_reason": reason,
               "cost_ai_usd": ai, "cost_telephony_usd": tel, "cost_usd": round(ai + tel, 5),
               "transferred": self.transferred, "transfer_target": self.transfer_target,
               "in_voicemail": self.in_voicemail, "usage_json": json.dumps(self.usage),
               "agent_version": self.agent.get("version")}
        if self.rec:
            try:
                res = await asyncio.to_thread(self.rec.finish)
                if res:
                    path, ctype = res
                    key = f"recordings/{tid}/{self.id}.{path.rsplit('.', 1)[-1]}"
                    await storage.aput_file(key, path, ctype)
                    upd["recording_path"] = key
            except Exception:
                log.exception("[%s] recording upload failed", self.tag)
            finally:
                self.rec.cleanup()
        try:
            await hot.ensure_inserted(self.id)
            await asyncio.to_thread(db.update, "calls", self.id, upd)
        except Exception:
            log.exception("[%s] could not save the final call state", self.tag)
        LOCAL.pop(self.id, None)
        await bus.live_remove(tid, self.id)
        await bus.publish(bus.mon(self.id), {"type": "ended", "reason": reason, "duration_sec": dur})
        try:
            await bus.kv_set(f"bridge:{self.id}", "", 1)
        except Exception:
            pass
        hot.finish(self.id)
        await postcall.process(self.id, self.agent, self.in_voicemail)


async def run_bridge(ws: WebSocket, provider: str, agent: dict, call: dict, prep: asyncio.Task | None = None):
    await CallSession(ws, provider, agent, call, prep).run()
