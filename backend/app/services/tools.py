"""Custom functions: the agent calls the client's APIs during a live call."""
import asyncio
import json
import logging
import re

import httpx

from .. import db
from . import netguard
from .templating import fill

log = logging.getLogger("tools")
NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{1,63}$")


def _loads(s, default):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


def load_tools(agent_id: str) -> list[dict]:
    rows = db.query("SELECT * FROM agent_tools WHERE agent_id = ? AND enabled = 1", agent_id)
    for r in rows:
        r["params"] = _loads(r.get("parameters_json"), [])
        r["headers"] = _loads(r.get("headers_json"), {})
    return rows


def to_schema(t: dict) -> dict:
    props, req = {}, []
    for p in t.get("params") or []:
        if not p.get("name"):
            continue
        typ = p.get("type") if p.get("type") in ("string", "number", "integer", "boolean") else "string"
        props[p["name"]] = {"type": typ, "description": p.get("description") or p["name"]}
        if p.get("required"):
            req.append(p["name"])
    schema = {"type": "object", "properties": props}
    if req:                       # an empty "required" list is left out - some APIs reject it
        schema["required"] = req
    return {"type": "function", "name": t["name"], "description": t.get("description") or t["name"], "parameters": schema}


async def run_tool(t: dict, args: dict, call_ctx: dict) -> str:
    """call_ctx = system + dynamic variables of the call; {{placeholders}} in URL and headers are filled from
    the variables and the arguments the agent collected."""
    ctx = {**call_ctx, **args}
    url = fill(t["url"], ctx, url=True)
    headers = {k: fill(str(v), ctx) for k, v in (t.get("headers") or {}).items()}
    timeout = max(2, min(int(t.get("timeout_sec") or 8), 30))
    retries = max(0, min(int(t.get("retries") or 0), 3))
    method = (t.get("method") or "POST").upper()
    body = {**args, "_call": {k: call_ctx.get(k) for k in ("call_id", "caller_number", "called_number", "direction")},
            "_variables": {k: v for k, v in call_ctx.items() if not k.startswith("_")}}
    last = ""
    for attempt in range(retries + 1):
        try:
            if method in ("GET", "DELETE"):
                u = httpx.URL(url)
                if args:                       # keep any ?query already in the URL, add the collected parameters
                    u = u.copy_merge_params({k: str(v) for k, v in args.items()})
                r = await netguard.request(method, str(u), headers=headers, timeout=timeout)
            else:
                r = await netguard.request(method, url, json=body, headers=headers, timeout=timeout)
            text = r.text[:4000]
            log.info("tool %s -> %s (attempt %d)", t["name"], r.status_code, attempt + 1)
            if r.status_code >= 500 and attempt < retries:
                last = f"API returned {r.status_code}"
                await asyncio.sleep(0.4 * (attempt + 1))
                continue
            if r.status_code >= 400:
                return json.dumps({"error": f"API returned {r.status_code}", "body": text[:500]})
            return text or json.dumps({"ok": True})
        except netguard.BlockedURL as e:
            return json.dumps({"error": str(e)})
        except Exception as e:
            last = str(e)
            log.warning("tool %s failed (attempt %d): %s", t["name"], attempt + 1, e)
            if attempt < retries:
                await asyncio.sleep(0.4 * (attempt + 1))
    return json.dumps({"error": "The system is not reachable right now.", "detail": last[:200]})
