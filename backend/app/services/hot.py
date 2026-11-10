"""Hot-path helpers.

Fabric SQL can take many seconds on a cold connection. A phone provider waits only a few seconds for our
answer webhook, so call set-up must never wait on the database. This module keeps what a call needs in
memory (numbers, agents, tenant limits, knowledge base) and writes call rows in the background.
"""
import asyncio
import logging
import time

from .. import db
from . import bus
from .aio import spawn
from .versions import effective

log = logging.getLogger("hot")

_cache: dict = {}
_numbers = {"ts": 0.0, "rows": [], "busy": False}
CALLS: dict[str, dict] = {}      # call_id -> call row (kept in memory while the call is live on this replica)
_inserts: dict[str, asyncio.Task] = {}
_refreshing: set = set()


# ---------------- small TTL cache ----------------

def _refresh_bg(key, fn, args):
    if key in _refreshing:
        return
    _refreshing.add(key)

    async def _do():
        try:
            _cache[key] = (time.time(), await asyncio.to_thread(fn, *args))
        except Exception as e:
            log.warning("background refresh of %s failed: %s", key if isinstance(key, str) else key[0], e)
        finally:
            _refreshing.discard(key)

    asyncio.create_task(_do())


async def cached(key, ttl: float, fn, *args, timeout: float = 3.0, swr: bool = False):
    """Return a cached value. When older than ttl: refresh it (swr=True returns the old value at once and
    refreshes in the background; otherwise wait up to `timeout` s, falling back to the old value)."""
    ent = _cache.get(key)
    if ent:
        if time.time() - ent[0] < ttl:
            return ent[1]
        if swr:
            _refresh_bg(key, fn, args)
            return ent[1]
    try:
        val = await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout)
        _cache[key] = (time.time(), val)
        return val
    except Exception as e:
        log.warning("cache load %s failed or timed out: %s", key if isinstance(key, str) else key[0], e)
        if ent:
            return ent[1]
        raise


def drop(key):
    _cache.pop(key, None)


def drop_prefix(prefix: str):
    for k in [k for k in _cache if (k[0] if isinstance(k, tuple) else k) == prefix]:
        _cache.pop(k, None)


def drop_agent(agent_id: str):
    """Call after an agent is edited / published so the next call uses the new settings (on every replica)."""
    _cache.pop(("agent", agent_id), None)
    _cache.pop(("kb", agent_id), None)
    spawn(bus.publish("cache", {"drop": "agent", "id": agent_id}))


async def cache_listener():
    """Other replicas tell us when an agent or the knowledge base changed."""
    if not bus.DISTRIBUTED:
        return
    while True:
        try:
            async for m in bus.subscribe("cache"):
                if m.get("drop") == "agent":
                    _cache.pop(("agent", m.get("id")), None)
                    _cache.pop(("kb", m.get("id")), None)
                elif m.get("drop") == "kb":
                    drop_prefix("kb")
                elif m.get("drop") == "numbers":
                    _numbers["ts"] = 0
        except Exception as e:
            log.warning("cache listener: %s", e)
            await asyncio.sleep(3)


async def get_agent_row(agent_id: str) -> dict | None:
    if not agent_id:
        return None
    try:
        return await cached(("agent", agent_id), 30, lambda: db.one("SELECT * FROM agents WHERE id = ?", agent_id),
                            swr=True)
    except Exception:
        return None


async def get_agent(agent_id: str, draft: bool = False) -> dict | None:
    """Effective agent config for a call: the published version (or the draft for browser tests)."""
    row = await get_agent_row(agent_id)
    return effective(row, draft) if row else None


# ---------------- phone numbers (stale-while-revalidate) ----------------

async def _refresh_numbers(raise_on_error: bool = False):
    try:
        rows = await asyncio.wait_for(
            asyncio.to_thread(db.query, "SELECT * FROM phone_numbers WHERE status = 'active'"), 8)
        _numbers.update(ts=time.time(), rows=rows)
    except Exception as e:
        log.warning("phone number refresh failed: %s", e)
        if raise_on_error:
            raise
    finally:
        _numbers["busy"] = False


async def numbers(force: bool = False) -> list[dict]:
    n = _numbers
    if n["rows"] and not force:
        if time.time() - n["ts"] > 15 and not n["busy"]:
            n["busy"] = True
            asyncio.create_task(_refresh_numbers())
        return n["rows"]
    await _refresh_numbers(raise_on_error=not n["rows"])
    return n["rows"]


def numbers_changed():
    _numbers["ts"] = 0
    spawn(bus.publish("cache", {"drop": "numbers"}))


# ---------------- tenant limits ----------------

def _load_tenant(tid: str):
    t = db.one("SELECT * FROM tenants WHERE id = ?", tid)
    if not t:
        return None
    month = db.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = db.scalar("SELECT ISNULL(SUM(duration_sec), 0) FROM calls WHERE tenant_id = ? AND started_at >= ?",
                     tid, month) or 0
    t["used_min"] = used / 60
    return t


async def tenant(tenant_id: str) -> dict | None:
    try:
        return await cached(("tenant", tenant_id), 60, _load_tenant, tenant_id, swr=True)
    except Exception:
        return None


async def capacity_error(tenant_id: str) -> str | None:
    """Message to speak to the caller if the tenant cannot take another call, else None."""
    t = await tenant(tenant_id)   # if the database is unreachable, let the call through rather than block it
    limit = 5
    if t:
        if t.get("status") != "active" or t["used_min"] >= (t.get("minutes_quota") or 0):
            return "Sorry, this service is temporarily unavailable."
        limit = t.get("max_concurrent_calls") or 5
    if await bus.live_count(tenant_id) >= limit:
        return "All our lines are busy right now. Please call again in a few minutes. Thank you."
    return None


# ---------------- call rows: insert in the background ----------------

def insert_call_bg(row: dict):
    cid = row["id"]
    CALLS[cid] = row

    async def _do():
        try:
            await asyncio.to_thread(db.insert, "calls", row)
        except Exception:
            log.exception("[%s] could not insert call row", cid[:8])

    _inserts[cid] = asyncio.create_task(_do())


async def ensure_inserted(cid: str):
    t = _inserts.get(cid)
    if t:
        try:
            await asyncio.wait_for(asyncio.shield(t), 30)
        except Exception:
            pass


def db_update_bg(cid: str, data: dict):
    async def _do():
        await ensure_inserted(cid)
        try:
            await asyncio.to_thread(db.update, "calls", cid, data)
        except Exception:
            log.exception("[%s] call update failed", cid[:8])

    asyncio.create_task(_do())


async def get_call(cid: str) -> dict | None:
    return CALLS.get(cid) or await asyncio.to_thread(db.one, "SELECT * FROM calls WHERE id = ?", cid)


def finish(cid: str):
    CALLS.pop(cid, None)
    _inserts.pop(cid, None)


# ---------------- warm-up & keep-alive ----------------

def _load_all():
    agents = db.query("SELECT * FROM agents")
    tenants = db.query("SELECT * FROM tenants")
    month = db.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = {r["tenant_id"]: r["s"] for r in db.query(
        "SELECT tenant_id, ISNULL(SUM(duration_sec), 0) AS s FROM calls WHERE started_at >= ? GROUP BY tenant_id",
        month)}
    return agents, tenants, used


async def warm(n: int = 8):
    """Open n database connections up front, keep Fabric awake, and preload agents / tenants / numbers."""
    def one():
        db.scalar("SELECT 1")
        time.sleep(0.3)   # keeps each worker thread busy so every thread opens its own connection

    t0 = time.time()
    results = await asyncio.gather(*[asyncio.to_thread(one) for _ in range(n)], asyncio.to_thread(_load_all),
                                   _refresh_numbers(), return_exceptions=True)
    loaded = results[n]
    if isinstance(loaded, Exception):
        log.warning("preload failed: %s", loaded)
    else:
        agents, tenants, used = loaded
        now = time.time()
        for a in agents:
            _cache[("agent", a["id"])] = (now, a)
        for t in tenants:
            t["used_min"] = (used.get(t["id"], 0) or 0) / 60
            _cache[("tenant", t["id"])] = (now, t)
    log.debug("database warm: %d connections, %d agents, %d numbers ready in %.1fs", n,
              0 if isinstance(loaded, Exception) else len(loaded[0]), len(_numbers["rows"]), time.time() - t0)


async def keepalive():
    while True:
        try:
            await warm(8)
        except Exception as e:
            log.warning("database warm-up failed: %s", e)
        await asyncio.sleep(150)
