"""Live call monitoring: list calls in progress, follow the transcript in real time, end a call."""
import asyncio

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from .. import db
from ..security import current_user, has_role, user_from_token
from ..services import bus

router = APIRouter(tags=["live"])


@router.get("/api/live")
async def live(u=Depends(current_user)):
    return sorted(await bus.live_list(u["tenant_id"]), key=lambda x: x.get("ts", 0), reverse=True)


@router.get("/api/admin/live")
async def live_all(u=Depends(current_user)):
    if not u["is_superadmin"]:
        return []
    return await bus.live_list(None)


@router.websocket("/ws/monitor/{call_id}")
async def monitor(ws: WebSocket, call_id: str, token: str = ""):
    await ws.accept()
    user = await asyncio.to_thread(user_from_token, token)
    call = user and await asyncio.to_thread(db.one, "SELECT id, tenant_id FROM calls WHERE id = ?", call_id)
    if not call or (call["tenant_id"] != user["tenant_id"] and not user["is_superadmin"]):
        await ws.close(code=4401)
        return
    # transcript so far, then live lines
    for t in await asyncio.to_thread(db.query, "SELECT role, text, offset_ms FROM call_transcripts WHERE call_id = ? "
                                               "ORDER BY offset_ms", call_id):
        await ws.send_json({"type": "transcript", **t})
    can_end = has_role(user, "member")
    await ws.send_json({"type": "hello", "can_end": can_end})

    async def pump():
        async for m in bus.subscribe(bus.mon(call_id)):
            await ws.send_json(m)
            if m.get("type") == "ended":
                return

    async def client():
        while True:
            m = await ws.receive_json()
            if m.get("type") == "end" and can_end:
                await bus.control(call_id, "end")

    tasks = [asyncio.create_task(pump()), asyncio.create_task(client())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()
        try:
            await ws.close()
        except Exception:
            pass
