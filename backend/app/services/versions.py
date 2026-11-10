"""Agent configuration: field list, defaults, draft vs published snapshot."""
import json

# Every setting that defines how an agent behaves on a call (and therefore belongs in a published version).
CONFIG_FIELDS = [
    "name", "first_message", "start_speaker", "system_prompt", "language", "auto_detect_language",
    "voice_type", "voice_name", "voice_rate", "speaker_profile_id", "model", "temperature", "max_call_seconds",
    "interrupt_sensitivity", "end_call_enabled", "begin_delay_ms", "silence_end_seconds", "reminder_seconds",
    "reminder_max", "voicemail_detection", "voicemail_action", "voicemail_message", "ivr_hangup",
    "screening_message", "ring_seconds", "dtmf_enabled", "dtmf_timeout_ms", "dtmf_terminator", "dtmf_max_digits",
    "recording_enabled", "boosted_keywords", "transfer_number", "transfer_mode", "transfer_ring_seconds",
    "analysis_fields_json", "variables_json",
]

DEFAULTS = {
    "first_message": "Hello! How can I help you today?", "start_speaker": "agent",
    "system_prompt": "You are a friendly assistant for our company.", "language": "auto",
    "auto_detect_language": True, "voice_type": "azure-standard", "voice_name": "en-IN-AartiNeural",
    "voice_rate": 1.0, "model": "gpt-realtime", "temperature": 0.8, "max_call_seconds": 600,
    "end_call_enabled": True, "interrupt_sensitivity": "medium", "status": "active",
    "begin_delay_ms": 0, "silence_end_seconds": 30, "reminder_seconds": 8, "reminder_max": 2,
    "voicemail_detection": True, "voicemail_action": "hangup", "voicemail_message": "", "ivr_hangup": True,
    "screening_message": "", "ring_seconds": 30, "dtmf_enabled": False, "dtmf_timeout_ms": 2500,
    "dtmf_terminator": "#", "dtmf_max_digits": 0, "recording_enabled": True, "boosted_keywords": "",
    "transfer_mode": "cold", "transfer_ring_seconds": 30, "analysis_fields_json": "[]", "variables_json": "{}",
}


def with_defaults(row: dict) -> dict:
    out = dict(row)
    for k, v in DEFAULTS.items():
        if out.get(k) is None:
            out[k] = v
    return out


def snapshot(agent_row: dict, tools: list | None = None) -> dict:
    """Frozen copy of an agent's settings (+ its custom functions) for a published version."""
    a = with_defaults(agent_row)
    cfg = {k: a.get(k) for k in CONFIG_FIELDS}
    if tools is None:
        try:
            from .tools import load_tools
            tools = load_tools(agent_row["id"])
        except Exception:
            tools = []
    cfg["tools"] = [{k: v for k, v in t.items() if k not in ("tenant_id", "created_at")} for t in tools]
    return cfg


def effective(agent_row: dict, draft: bool = False) -> dict:
    """The config a call should use: the published snapshot, or the live draft for browser tests.
    id / tenant / status always come from the live row, so pausing an agent applies at once."""
    base = {"id": agent_row["id"], "tenant_id": agent_row["tenant_id"], "status": agent_row.get("status") or "active",
            "published_version": agent_row.get("published_version")}
    pub = agent_row.get("published_json")
    if pub and not draft:
        try:
            cfg = json.loads(pub)
            return {**with_defaults(cfg), **base, "version": agent_row.get("published_version")}
        except Exception:
            pass
    cfg = with_defaults({k: agent_row.get(k) for k in CONFIG_FIELDS})
    cfg["tools"] = None          # loaded from the database at call time
    return {**cfg, **base, "version": None}


def jload(s, default):
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s) if s else default
    except Exception:
        return default
