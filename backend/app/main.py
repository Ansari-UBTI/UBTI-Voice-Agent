import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, migrate
from .config import settings
from .routers import (admin, agents, analytics, auth, calls, campaigns, developer, knowledge, live, numbers,
                      telephony, tools, voices)
from .security import check_production_settings, hash_password
from .services import aio, bus, hot, scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("azure", "httpx", "websockets"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("ubti")


def seed_admin():
    if not (settings.ADMIN_EMAIL and settings.ADMIN_PASSWORD):
        return
    if db.one("SELECT id FROM users WHERE email = ?", settings.ADMIN_EMAIL.lower()):
        return
    t = db.insert("tenants", {"name": "UBTI Platform", "plan": "enterprise", "status": "active", "country": "IN",
                              "minutes_quota": 100000, "max_concurrent_calls": 50, "created_at": db.now()})
    db.insert("users", {"tenant_id": t["id"], "email": settings.ADMIN_EMAIL.lower(), "name": "UBTI Admin",
                        "role": "owner", "password_hash": hash_password(settings.ADMIN_PASSWORD),
                        "is_superadmin": True, "is_active": True, "created_at": db.now()})
    log.info("Seeded super admin %s", settings.ADMIN_EMAIL)


def close_stale_calls():
    """Calls left 'in-progress' by a crash/restart. With several replicas (Redis) only clearly dead rows."""
    if bus.DISTRIBUTED:
        n = db.execute("UPDATE calls SET status = 'failed', ended_reason = 'server_restart', ended_at = SYSUTCDATETIME() "
                       "WHERE status = 'in-progress' AND started_at < DATEADD(hour, -3, SYSUTCDATETIME())")
    else:
        n = db.execute("UPDATE calls SET status = 'failed', ended_reason = 'server_restart', ended_at = SYSUTCDATETIME() "
                       "WHERE status = 'in-progress'")
    if n:
        log.info("Closed %s stale calls from previous run", n)


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_production_settings()
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    os.makedirs(settings.TMP_DIR, exist_ok=True)
    loop = asyncio.get_running_loop()
    aio.set_main_loop(loop)
    # A small fixed pool = a small number of long-lived, already-open database connections.
    loop.set_default_executor(ThreadPoolExecutor(max_workers=16, thread_name_prefix="db"))
    try:
        if settings.AUTO_MIGRATE:
            await asyncio.to_thread(migrate.run)
        await asyncio.to_thread(seed_admin)
        await asyncio.to_thread(close_stale_calls)
        log.info("Connected to Fabric SQL: %s", settings.FABRIC_SQL_DATABASE)
    except Exception as e:
        log.error("Fabric SQL not reachable at startup: %s", e)
    bg = [asyncio.create_task(hot.keepalive()),      # warms connections now and every 2.5 minutes
          asyncio.create_task(scheduler.loop()),      # batch calls, stuck calls, retention
          asyncio.create_task(hot.cache_listener())]
    log.info("%s ready (%s)", settings.APP_NAME, "Redis - multi-replica" if bus.DISTRIBUTED else "single replica")
    yield
    for t in bg:
        t.cancel()


app = FastAPI(title=settings.APP_NAME, version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[settings.FRONTEND_ORIGIN, "http://localhost:5173",
                                                  "http://127.0.0.1:5173"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

for r in (auth, agents, tools, voices, numbers, calls, campaigns, knowledge, analytics, developer, admin, telephony,
          live):
    app.include_router(r.router)
app.include_router(calls.files_router)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME}


# ---------- the built React dashboard (single container deployment) ----------
if os.path.isdir(settings.STATIC_DIR):
    assets = os.path.join(settings.STATIC_DIR, "assets")
    if os.path.isdir(assets):
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "telephony/", "media/", "ws/", "recordings/")):
            raise HTTPException(404)
        f = os.path.normpath(os.path.join(settings.STATIC_DIR, path))
        if path and f.startswith(os.path.normpath(settings.STATIC_DIR)) and os.path.isfile(f):
            return FileResponse(f)
        return FileResponse(os.path.join(settings.STATIC_DIR, "index.html"))
