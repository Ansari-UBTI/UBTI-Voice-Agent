"""CRUD + test for agent custom functions. Changes go to the draft; publish the agent to use them on live calls."""
import asyncio
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db
from ..security import current_user, member
from ..services import hot, netguard
from ..services.templating import system_vars
from ..services.tools import NAME_RE, load_tools, run_tool

router = APIRouter(prefix="/api/agents/{agent_id}/tools", tags=["custom functions"])
RESERVED = {"end_call", "transfer_call", "voicemail_detected", "ivr_detected", "search_knowledge"}


class Param(BaseModel):
    name: str
    type: str = "string"
    description: str = ""
    required: bool = True


class ToolIn(BaseModel):
    name: str
    description: str
    method: str = "POST"
    url: str
    headers: dict = {}
    parameters: List[Param] = []
    wait_message: Optional[str] = None
    timeout_sec: int = 8
    retries: int = 1
    enabled: bool = True


class TestIn(BaseModel):
    args: dict = {}


def _agent(agent_id, u):
    if not db.one("SELECT id FROM agents WHERE id = ? AND tenant_id = ?", agent_id, u["tenant_id"]):
        raise HTTPException(404, "Agent not found")


def _touch(agent_id):
    db.execute("UPDATE agents SET updated_at = ? WHERE id = ?", db.now(), agent_id)
    hot.drop(("tools", agent_id))
    hot.drop_agent(agent_id)


async def _row(b: ToolIn) -> dict:
    if not NAME_RE.match(b.name) or b.name in RESERVED:
        raise HTTPException(400, "Name must be like check_order_status (letters, numbers, underscore)")
    if b.method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        raise HTTPException(400, "Method must be GET, POST, PUT, PATCH or DELETE")
    if "{{" not in b.url:                       # URLs with placeholders are checked at call time
        try:
            await netguard.check(b.url)
        except netguard.BlockedURL as e:
            raise HTTPException(400, str(e))
    elif not b.url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL must start with http(s)://")
    return {"name": b.name, "description": b.description, "method": b.method.upper(), "url": b.url,
            "headers_json": json.dumps(b.headers or {}),
            "parameters_json": json.dumps([p.model_dump() for p in b.parameters if p.name.strip()]),
            "wait_message": b.wait_message or None, "timeout_sec": max(2, min(b.timeout_sec, 30)),
            "retries": max(0, min(b.retries, 3)), "enabled": b.enabled}


def _out(r):
    r["headers"] = json.loads(r.pop("headers_json") or "{}")
    r["parameters"] = json.loads(r.pop("parameters_json") or "[]")
    return r


@router.get("")
def list_tools(agent_id: str, u=Depends(current_user)):
    _agent(agent_id, u)
    return [_out(r) for r in db.query("SELECT * FROM agent_tools WHERE agent_id = ? AND tenant_id = ? "
                                      "ORDER BY created_at", agent_id, u["tenant_id"])]


def _create(agent_id, row, u):
    _agent(agent_id, u)
    if db.one("SELECT id FROM agent_tools WHERE agent_id = ? AND name = ?", agent_id, row["name"]):
        raise HTTPException(400, "A function with this name already exists")
    r = db.insert("agent_tools", {**row, "tenant_id": u["tenant_id"], "agent_id": agent_id, "created_at": db.now()})
    _touch(agent_id)
    db.audit(u["tenant_id"], u["id"], "tool.create", row["name"])
    return {"id": r["id"]}


def _update(agent_id, tool_id, row, u):
    _agent(agent_id, u)
    if db.one("SELECT id FROM agent_tools WHERE agent_id = ? AND name = ? AND id <> ?", agent_id, row["name"], tool_id):
        raise HTTPException(400, "A function with this name already exists")
    if not db.update("agent_tools", tool_id, row, u["tenant_id"]):
        raise HTTPException(404, "Function not found")
    _touch(agent_id)
    return {"ok": True}


@router.post("")
async def create_tool(agent_id: str, b: ToolIn, u=Depends(member)):
    return await asyncio.to_thread(_create, agent_id, await _row(b), u)


@router.put("/{tool_id}")
async def update_tool(agent_id: str, tool_id: str, b: ToolIn, u=Depends(member)):
    return await asyncio.to_thread(_update, agent_id, tool_id, await _row(b), u)


@router.delete("/{tool_id}")
def delete_tool(agent_id: str, tool_id: str, u=Depends(member)):
    db.execute("DELETE FROM agent_tools WHERE id = ? AND agent_id = ? AND tenant_id = ?", tool_id, agent_id,
               u["tenant_id"])
    _touch(agent_id)
    return {"ok": True}


@router.post("/{tool_id}/test")
async def test_tool(agent_id: str, tool_id: str, b: TestIn, u=Depends(member)):
    await asyncio.to_thread(_agent, agent_id, u)
    t = next((x for x in await asyncio.to_thread(load_tools, agent_id) if x["id"] == tool_id), None)
    if not t:
        raise HTTPException(404, "Function not found or disabled")
    ctx = system_vars({"id": "test", "direction": "test", "from_number": "+910000000000", "to_number": ""})
    result = await run_tool(t, b.args, ctx)
    return {"result": result}
