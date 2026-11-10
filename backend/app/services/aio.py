"""Start background work safely from anywhere.

FastAPI runs plain `def` endpoints in worker threads, where asyncio.get_event_loop() raises
"There is no current event loop" (this crashed 'Start campaign' and 'Approve voice' before)."""
import asyncio
import logging

log = logging.getLogger("aio")
MAIN_LOOP: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop):
    global MAIN_LOOP
    MAIN_LOOP = loop


def spawn(coro):
    try:
        return asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        if MAIN_LOOP is None:
            coro.close()
            log.error("no event loop to run background task")
            return None
        return asyncio.run_coroutine_threadsafe(coro, MAIN_LOOP)
