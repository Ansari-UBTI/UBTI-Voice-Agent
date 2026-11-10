"""Per-call cost from real usage: Voice Live token counts x model rates + telephony minutes x carrier rate.

The default numbers are placeholders in USD. Put your own Azure price sheet / carrier rates in PRICING_JSON, e.g.
{"models": {"gpt-realtime": {"audio_in": 40, "audio_out": 80}}, "telephony_per_min": {"vobiz": 0.008}}"""
from ..config import settings

# USD per 1M tokens
MODELS = {
    "gpt-realtime": {"text_in": 4.0, "text_in_cached": 0.4, "audio_in": 32.0, "audio_in_cached": 0.4,
                     "text_out": 16.0, "audio_out": 64.0},
    "gpt-realtime-mini": {"text_in": 0.6, "text_in_cached": 0.06, "audio_in": 10.0, "audio_in_cached": 0.3,
                          "text_out": 2.4, "audio_out": 20.0},
    "gpt-4o": {"text_in": 2.5, "text_in_cached": 1.25, "audio_in": 2.5, "audio_in_cached": 1.25,
               "text_out": 10.0, "audio_out": 10.0},
    "gpt-4o-mini": {"text_in": 0.15, "text_in_cached": 0.075, "audio_in": 0.15, "audio_in_cached": 0.075,
                    "text_out": 0.6, "audio_out": 0.6},
}
# USD per minute: speech in/out for cascaded models (Azure Speech), carrier minutes
SPEECH_PER_MIN = {"gpt-4o": 0.03, "gpt-4o-mini": 0.03}
TELEPHONY_PER_MIN = {"vobiz": 0.006, "twilio": 0.014, "acs": 0.013, "web": 0.0}
ANALYSIS = {"text_in": 0.15, "text_out": 0.6}           # post-call model (gpt-4o-mini)


def _cfg():
    o = settings.pricing_override
    models = {**MODELS, **{k: {**MODELS.get(k, {}), **v} for k, v in (o.get("models") or {}).items()}}
    return models, {**TELEPHONY_PER_MIN, **(o.get("telephony_per_min") or {})}, \
        {**SPEECH_PER_MIN, **(o.get("speech_per_min") or {})}, {**ANALYSIS, **(o.get("analysis") or {})}


def empty_usage() -> dict:
    return {"text_in": 0, "text_in_cached": 0, "audio_in": 0, "audio_in_cached": 0, "text_out": 0, "audio_out": 0,
            "responses": 0}


def add_usage(acc: dict, u: dict | None):
    """Add one response.done usage block (RealtimeUsage) to the running total."""
    if not u:
        return
    i, o = u.get("input_token_details") or {}, u.get("output_token_details") or {}
    cached = i.get("cached_tokens") or 0
    cached_d = i.get("cached_tokens_details") or {}
    c_text = cached_d.get("text_tokens", cached if not cached_d else 0) or 0
    c_audio = cached_d.get("audio_tokens", 0) or 0
    acc["text_in"] += max(0, (i.get("text_tokens") or 0) - c_text)
    acc["audio_in"] += max(0, (i.get("audio_tokens") or 0) - c_audio)
    acc["text_in_cached"] += c_text
    acc["audio_in_cached"] += c_audio
    acc["text_out"] += o.get("text_tokens") or 0
    acc["audio_out"] += o.get("audio_tokens") or 0
    acc["responses"] += 1


def call_cost(model: str, usage: dict, provider: str, duration_sec: int) -> tuple[float, float]:
    """Returns (ai_usd, telephony_usd)."""
    models, tel, speech, _ = _cfg()
    rates = models.get(model) or models["gpt-realtime"]
    ai = sum((usage.get(k) or 0) * rates.get(k, 0) for k in rates) / 1_000_000
    ai += speech.get(model, 0) * duration_sec / 60
    return round(ai, 5), round(tel.get(provider, 0) * duration_sec / 60, 5)


def analysis_cost(prompt_tokens: int, completion_tokens: int) -> float:
    a = _cfg()[3]
    return round((prompt_tokens * a["text_in"] + completion_tokens * a["text_out"]) / 1_000_000, 5)
