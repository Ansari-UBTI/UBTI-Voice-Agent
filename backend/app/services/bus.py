"""Cross-instance coordination.

Without REDIS_URL everything lives in this process (run ONE replica). With REDIS_URL, any replica can receive a
provider webhook, a dashboard "end call" click or a monitor connection for a call that is bridged on another
replica: messages travel over Redis pub/sub, and the live-call registry, short-lived keys and locks are in Redis.
"""
import asyncio
import json
import logging
import time
from typing import AsyncIterator

from ..config import settings

log = logging.getLogger("bus")
LIVE_TTL = 3 * 3600


class _Memory:
    def __init__(self):
        self.subs: dict[str, set[asyncio.Queue]] = {}
        self.kv: dict[str, tuple[float, str]] = {}
        self.live: dict[str, dict[str, str]] = {}

    async def publish(self, ch, msg):
        for q in list(self.subs.get(ch, ())):
            q.put_nowait(msg)

    async def subscribe(self, ch) -> AsyncIterator[str]:
        q: asyncio.Queue = asyncio.Queue()
        self.subs.setdefault(ch, set()).add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self.subs.get(ch, set()).discard(q)
            if not self.subs.get(ch):
                self.subs.pop(ch, None)

    async def set(self, key, val, ttl):
        self.kv[key] = (time.time() + ttl, val)

    async def get(self, key):
        e = self.kv.get(key)
        if not e or e[0] < time.time():
            self.kv.pop(key, None)
            return None
        return e[1]

    async def setnx(self, key, val, ttl) -> bool:
        if await self.get(key) is not None:
            return False
        await self.set(key, val, ttl)
        return True

    async def delete(self, key):
        self.kv.pop(key, None)

    async def hset(self, h, field, val):
        self.live.setdefault(h, {})[field] = val

    async def hdel(self, h, field):
        self.live.get(h, {}).pop(field, None)

    async def hgetall(self, h) -> dict:
        return dict(self.live.get(h, {}))


class _Redis:
    def __init__(self, url):
        import redis.asyncio as redis
        self.r = redis.from_url(url, decode_responses=True)

    async def publish(self, ch, msg):
        await self.r.publish(ch, msg)

    async def subscribe(self, ch) -> AsyncIterator[str]:
        ps = self.r.pubsub()
        await ps.subscribe(ch)
        try:
            async for m in ps.listen():
                if m.get("type") == "message":
                    yield m["data"]
        finally:
            try:
                await ps.unsubscribe(ch)
                await ps.aclose()
            except Exception:
                pass

    async def set(self, key, val, ttl):
        await self.r.set(key, val, ex=int(ttl))

    async def get(self, key):
        return await self.r.get(key)

    async def setnx(self, key, val, ttl) -> bool:
        return bool(await self.r.set(key, val, ex=int(ttl), nx=True))

    async def delete(self, key):
        await self.r.delete(key)

    async def hset(self, h, field, val):
        await self.r.hset(h, field, val)
        await self.r.expire(h, LIVE_TTL)

    async def hdel(self, h, field):
        await self.r.hdel(h, field)

    async def hgetall(self, h) -> dict:
        return await self.r.hgetall(h)


_b = _Redis(settings.REDIS_URL) if settings.REDIS_URL else _Memory()
DISTRIBUTED = bool(settings.REDIS_URL)


# ---------------- messages ----------------

async def publish(channel: str, msg: dict):
    try:
        await _b.publish(channel, json.dumps(msg, default=str))
    except Exception as e:
        log.warning("publish %s failed: %s", channel, e)


async def subscribe(channel: str) -> AsyncIterator[dict]:
    async for raw in _b.subscribe(channel):
        try:
            yield json.loads(raw)
        except Exception:
            continue


def ctl(call_id: str) -> str:
    """Control channel of one call: hangup, end, voicemail, dtmf, participant events."""
    return f"ctl:{call_id}"


def mon(call_id: str) -> str:
    """Monitor channel of one call: transcript lines and status for the live view."""
    return f"mon:{call_id}"


async def control(call_id: str, type_: str, **data):
    await publish(ctl(call_id), {"type": type_, **data})


# ---------------- small shared keys & locks ----------------

async def kv_set(key: str, val: dict | str, ttl: float = 3600):
    await _b.set(key, json.dumps(val) if isinstance(val, dict) else val, ttl)


async def kv_get(key: str, as_json: bool = True):
    v = await _b.get(key)
    if v is None or not as_json:
        return v
    try:
        return json.loads(v)
    except Exception:
        return v


async def lock(name: str, ttl: float) -> bool:
    return await _b.setnx(f"lock:{name}", str(time.time()), ttl)


async def unlock(name: str):
    await _b.delete(f"lock:{name}")


# ---------------- live call registry ----------------

async def live_add(tenant_id: str, call_id: str, info: dict):
    await _b.hset(f"live:{tenant_id}", call_id, json.dumps({**info, "ts": time.time()}, default=str))
    await _b.hset("live:_all", call_id, tenant_id)


async def live_remove(tenant_id: str, call_id: str):
    await _b.hdel(f"live:{tenant_id}", call_id)
    await _b.hdel("live:_all", call_id)


async def live_list(tenant_id: str | None = None) -> list[dict]:
    tids = [tenant_id] if tenant_id else sorted(set((await _b.hgetall("live:_all")).values()))
    out, now = [], time.time()
    for tid in tids:
        for cid, raw in (await _b.hgetall(f"live:{tid}")).items():
            try:
                info = json.loads(raw)
            except Exception:
                continue
            if now - info.get("ts", now) > LIVE_TTL:         # left behind by a crash
                await live_remove(tid, cid)
                continue
            out.append({"call_id": cid, "tenant_id": tid, **info})
    return out


async def live_count(tenant_id: str) -> int:
    return len(await live_list(tenant_id))
