"""Dynamic variables: {{name}} placeholders in prompts, first message, voicemail text and custom functions."""
import datetime
import re
from urllib.parse import quote

VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def system_vars(call: dict, tz: str | None = None, agent_name: str = "") -> dict:
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        now = now.astimezone(ZoneInfo(tz or "Asia/Kolkata"))
    except Exception:
        pass
    inbound = call.get("direction") == "inbound"
    return {
        "call_id": call.get("id", ""), "direction": call.get("direction", ""),
        "caller_number": call.get("from_number") or "",           # kept for existing custom functions
        "called_number": call.get("to_number") or "",
        "user_number": (call.get("from_number") if inbound else call.get("to_number")) or "",
        "agent_number": (call.get("to_number") if inbound else call.get("from_number")) or "",
        "agent_name": agent_name,
        "current_time": now.strftime("%I:%M %p").lstrip("0"), "current_date": now.strftime("%d %B %Y"),
        "day_of_week": now.strftime("%A"), "current_hour": str(now.hour),
    }


def fill(text: str | None, ctx: dict, url: bool = False) -> str:
    """Replace {{var}}. Unknown variables become empty so the agent never reads out raw braces.
    In URLs values are percent-encoded (a '+' in +971... must not turn into a space)."""
    def val(m):
        v = ctx.get(m.group(1))
        v = "" if v is None else str(v)
        return quote(v, safe="") if url else v
    return VAR_RE.sub(val, text or "")


def names(text: str | None) -> list[str]:
    return sorted(set(VAR_RE.findall(text or "")))


def clean_vars(d) -> dict:
    """Variables from API / CSV: string keys and values only, names like customer_name."""
    out = {}
    for k, v in (d or {}).items():
        k = re.sub(r"[^A-Za-z0-9_]", "_", str(k).strip()).strip("_").lower()
        if k and v is not None and str(v).strip() != "":
            out[k[:64]] = str(v)[:1000]
    return out
