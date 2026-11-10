import json
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db
from ..security import current_user, member
from ..services import hot
from ..services.catalog import LANGUAGES, MODELS, TEMPLATES
from ..services.templating import names
from ..services.tools import load_tools
from ..services.versions import CONFIG_FIELDS, DEFAULTS, jload, snapshot

router = APIRouter(prefix="/api/agents", tags=["agents"])


class AnalysisField(BaseModel):
    name: str
    type: str = "string"            # string | number | boolean | enum
    description: str = ""
    options: List[str] = []


class AgentIn(BaseModel):
    name: Optional[str] = None
    first_message: Optional[str] = None
    start_speaker: Optional[str] = None
    system_prompt: Optional[str] = None
    language: Optional[str] = None
    auto_detect_language: Optional[bool] = None
    voice_type: Optional[str] = None
    voice_name: Optional[str] = None
    voice_rate: Optional[float] = None
    speaker_profile_id: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_call_seconds: Optional[int] = None
    transfer_number: Optional[str] = None
    transfer_mode: Optional[str] = None
    transfer_ring_seconds: Optional[int] = None
    end_call_enabled: Optional[bool] = None
    interrupt_sensitivity: Optional[str] = None
    status: Optional[str] = None
    begin_delay_ms: Optional[int] = None
    silence_end_seconds: Optional[int] = None
    reminder_seconds: Optional[int] = None
    reminder_max: Optional[int] = None
    voicemail_detection: Optional[bool] = None
    voicemail_action: Optional[str] = None
    voicemail_message: Optional[str] = None
    ivr_hangup: Optional[bool] = None
    screening_message: Optional[str] = None
    ring_seconds: Optional[int] = None
    dtmf_enabled: Optional[bool] = None
    dtmf_timeout_ms: Optional[int] = None
    dtmf_terminator: Optional[str] = None
    dtmf_max_digits: Optional[int] = None
    recording_enabled: Optional[bool] = None
    boosted_keywords: Optional[str] = None
    analysis_fields: Optional[List[AnalysisField]] = None
    variables: Optional[dict[str, Any]] = None


class PublishIn(BaseModel):
    note: Optional[str] = None


LIMITS = {"max_call_seconds": (30, 7200), "silence_end_seconds": (0, 3600), "reminder_seconds": (0, 120),
          "reminder_max": (0, 10), "ring_seconds": (10, 120), "transfer_ring_seconds": (10, 120),
          "dtmf_timeout_ms": (500, 20000), "dtmf_max_digits": (0, 50), "begin_delay_ms": (0, 5000),
          "temperature": (0.6, 1.2), "voice_rate": (0.5, 2.0)}
CHOICES = {"start_speaker": ("agent", "user"), "voicemail_action": ("hangup", "leave_message"),
           "transfer_mode": ("cold", "warm"), "interrupt_sensitivity": ("low", "medium", "high"),
           "status": ("active", "paused")}


def _clean(b: AgentIn) -> dict:
    data = {k: v for k, v in b.model_dump(exclude={"analysis_fields", "variables"}).items() if v is not None}
    for k, (lo, hi) in LIMITS.items():
        if k in data:
            data[k] = max(lo, min(hi, data[k]))
    for k, ok in CHOICES.items():
        if k in data and data[k] not in ok:
            raise HTTPException(400, f"{k} must be one of {', '.join(ok)}")
    if "dtmf_terminator" in data and data["dtmf_terminator"] not in ("", "#", "*", *"0123456789"):
        raise HTTPException(400, "Termination key must be 0-9, # or *")
    if b.analysis_fields is not None:
        fields = []
        for f in b.analysis_fields:
            n = "".join(ch if ch.isalnum() else "_" for ch in f.name.strip().lower()).strip("_")
            if n:
                fields.append({"name": n[:40], "type": f.type if f.type in ("string", "number", "boolean", "enum")
                               else "string", "description": f.description[:300], "options": f.options[:20]})
        data["analysis_fields_json"] = json.dumps(fields)
    if b.variables is not None:
        data["variables_json"] = json.dumps({str(k): str(v) for k, v in b.variables.items() if str(k).strip()})
    for k in ("transfer_number",):
        if k in b.model_fields_set and getattr(b, k) == "":
            data[k] = None
    return data


def _out(a: dict) -> dict:
    a = dict(a)
    pub = jload(a.pop("published_json", None), None)
    a["analysis_fields"] = jload(a.pop("analysis_fields_json", None), [])
    a["variables"] = jload(a.pop("variables_json", None), {})
    for k, v in DEFAULTS.items():
        if a.get(k) is None and k not in ("analysis_fields_json", "variables_json"):
            a[k] = v
    a["has_unpublished_changes"] = pub is None or bool(
        a.get("updated_at") and a.get("published_at") and str(a["updated_at"]) > str(a["published_at"]))
    text = " ".join(str(a.get(k) or "") for k in ("system_prompt", "first_message", "voicemail_message",
                                                  "screening_message"))
    a["variables_used"] = names(text)
    return a


@router.get("/meta")
def meta(u=Depends(current_user)):
    return {"languages": LANGUAGES, "models": MODELS, "templates": TEMPLATES,
            "system_variables": ["call_id", "direction", "user_number", "agent_number", "caller_number",
                                 "called_number", "agent_name", "current_time", "current_date", "day_of_week",
                                 "current_hour"]}


@router.get("")
def list_agents(u=Depends(current_user)):
    rows = db.query("""SELECT a.*, (SELECT COUNT(*) FROM calls c WHERE c.agent_id = a.id) AS total_calls,
                       (SELECT COUNT(*) FROM phone_numbers p WHERE p.agent_id = a.id) AS numbers
                       FROM agents a WHERE a.tenant_id = ? ORDER BY a.updated_at DESC""", u["tenant_id"])
    return [_out(r) for r in rows]


@router.post("")
def create_agent(b: AgentIn, template: Optional[str] = None, u=Depends(member)):
    data = {**DEFAULTS, **_clean(b)}
    tpl = next((t for t in TEMPLATES if t["id"] == template), None)
    if tpl:
        data["first_message"] = tpl["first_message"].replace("{company}", u["tenant_name"])
        data["system_prompt"] = tpl["system_prompt"].replace("{company}", u["tenant_name"])
        data.setdefault("name", tpl["name"])
        if template in ("outbound_lead", "collections"):
            data["start_speaker"] = "agent"
    data["name"] = data.get("name") or "New Agent"
    data.update({"tenant_id": u["tenant_id"], "created_at": db.now(), "updated_at": db.now()})
    a = db.insert("agents", data)
    _publish(a["id"], u, "Initial version")
    db.audit(u["tenant_id"], u["id"], "agent.create", data["name"])
    return get_agent(a["id"], u)


@router.get("/{agent_id}")
def get_agent(agent_id: str, u=Depends(current_user)):
    a = db.one("SELECT * FROM agents WHERE id = ? AND tenant_id = ?", agent_id, u["tenant_id"])
    if not a:
        raise HTTPException(404, "Agent not found")
    return _out(a)


@router.put("/{agent_id}")
def update_agent(agent_id: str, b: AgentIn, u=Depends(member)):
    """Saves the DRAFT. Live calls keep using the published version until you publish."""
    get_agent(agent_id, u)
    data = _clean(b)
    if set(data) - {"status"}:                # pausing / activating is not a config change - no new draft
        data["updated_at"] = db.now()
    db.update("agents", agent_id, data, u["tenant_id"])
    hot.drop_agent(agent_id)          # status (pause) and browser tests apply immediately
    db.audit(u["tenant_id"], u["id"], "agent.update", agent_id)
    return get_agent(agent_id, u)


def _publish(agent_id: str, u: dict, note: str | None) -> int:
    a = db.one("SELECT * FROM agents WHERE id = ? AND tenant_id = ?", agent_id, u["tenant_id"])
    cfg = snapshot(a, load_tools(agent_id))
    v = (db.scalar("SELECT MAX(version) FROM agent_versions WHERE agent_id = ?", agent_id) or 0) + 1
    payload = json.dumps(cfg, default=str)
    db.insert("agent_versions", {"tenant_id": u["tenant_id"], "agent_id": agent_id, "version": v,
                                 "config_json": payload, "note": (note or "")[:500], "created_by": u["id"],
                                 "created_at": db.now()})
    db.update("agents", agent_id, {"published_json": payload, "published_version": v, "published_at": db.now()})
    hot.drop_agent(agent_id)
    return v


@router.post("/{agent_id}/publish")
def publish(agent_id: str, b: PublishIn, u=Depends(member)):
    get_agent(agent_id, u)
    v = _publish(agent_id, u, b.note)
    db.audit(u["tenant_id"], u["id"], "agent.publish", f"{agent_id} v{v}")
    return {"version": v, "agent": get_agent(agent_id, u)}


@router.get("/{agent_id}/versions")
def versions(agent_id: str, u=Depends(current_user)):
    get_agent(agent_id, u)
    return db.query("""SELECT v.id, v.version, v.note, v.created_at, us.name AS created_by_name
                       FROM agent_versions v LEFT JOIN users us ON us.id = v.created_by
                       WHERE v.agent_id = ? ORDER BY v.version DESC""", agent_id)


@router.get("/{agent_id}/versions/{version}")
def version_detail(agent_id: str, version: int, u=Depends(current_user)):
    get_agent(agent_id, u)
    v = db.one("SELECT * FROM agent_versions WHERE agent_id = ? AND version = ?", agent_id, version)
    if not v:
        raise HTTPException(404)
    v["config"] = jload(v.pop("config_json"), {})
    return v


@router.post("/{agent_id}/versions/{version}/restore")
def restore(agent_id: str, version: int, publish_now: bool = False, u=Depends(member)):
    """Copy an old version into the draft (and optionally publish it = rollback)."""
    get_agent(agent_id, u)
    v = db.one("SELECT config_json FROM agent_versions WHERE agent_id = ? AND version = ?", agent_id, version)
    if not v:
        raise HTTPException(404)
    cfg = jload(v["config_json"], {})
    db.update("agents", agent_id, {**{k: cfg.get(k) for k in CONFIG_FIELDS if k in cfg}, "updated_at": db.now()},
              u["tenant_id"])
    # restore the functions of that version too
    db.execute("DELETE FROM agent_tools WHERE agent_id = ? AND tenant_id = ?", agent_id, u["tenant_id"])
    for t in cfg.get("tools") or []:
        db.insert("agent_tools", {"tenant_id": u["tenant_id"], "agent_id": agent_id, "name": t["name"],
                                  "description": t.get("description"), "method": t.get("method"), "url": t.get("url"),
                                  "headers_json": json.dumps(t.get("headers") or jload(t.get("headers_json"), {})),
                                  "parameters_json": json.dumps(t.get("params") or jload(t.get("parameters_json"), [])),
                                  "wait_message": t.get("wait_message"), "timeout_sec": t.get("timeout_sec"),
                                  "retries": t.get("retries") or 0, "enabled": True, "created_at": db.now()})
    hot.drop_agent(agent_id)
    out = {"restored": version}
    if publish_now:
        out["version"] = _publish(agent_id, u, f"Rollback to v{version}")
    db.audit(u["tenant_id"], u["id"], "agent.restore", f"{agent_id} v{version}")
    return {**out, "agent": get_agent(agent_id, u)}


@router.post("/{agent_id}/duplicate")
def duplicate(agent_id: str, u=Depends(member)):
    a = db.one("SELECT * FROM agents WHERE id = ? AND tenant_id = ?", agent_id, u["tenant_id"])
    if not a:
        raise HTTPException(404)
    for k in ("id", "published_json", "published_version", "published_at"):
        a.pop(k, None)
    a.update({"name": a["name"] + " (copy)", "created_at": db.now(), "updated_at": db.now()})
    new = db.insert("agents", a)
    for t in load_tools(agent_id):
        db.insert("agent_tools", {k: v for k, v in t.items() if k not in ("id", "params", "headers")} |
                  {"agent_id": new["id"], "created_at": db.now()})
    _publish(new["id"], u, "Duplicated")
    return get_agent(new["id"], u)


@router.delete("/{agent_id}")
def delete_agent(agent_id: str, u=Depends(member)):
    get_agent(agent_id, u)
    db.execute("UPDATE phone_numbers SET agent_id = NULL WHERE agent_id = ? AND tenant_id = ?", agent_id, u["tenant_id"])
    db.execute("DELETE FROM agent_tools WHERE agent_id = ? AND tenant_id = ?", agent_id, u["tenant_id"])
    db.execute("DELETE FROM agent_versions WHERE agent_id = ? AND tenant_id = ?", agent_id, u["tenant_id"])
    db.execute("DELETE FROM agents WHERE id = ? AND tenant_id = ?", agent_id, u["tenant_id"])
    hot.drop_agent(agent_id)
    hot.numbers_changed()
    db.audit(u["tenant_id"], u["id"], "agent.delete", agent_id)
    return {"ok": True}
