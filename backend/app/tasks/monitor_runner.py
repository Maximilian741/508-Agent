"""Periodic runner for scheduled site monitors.

Mirrors :mod:`app.tasks.cleanup`: a single ``asyncio`` task started at app
startup. Every worker process runs its own copy — that is SAFE here because
work is claimed through the DB lease in :mod:`app.services.monitoring`
(``claim_due_monitor``), so exactly one worker ever executes a given monitor.

Deliberately paced: one monitor per tick. Monitors are outbound fetches of
someone else's site, so we trickle rather than stampede, and a 60s tick easily
keeps up with daily/weekly schedules.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_TICK_SECONDS = 60
_started = False


async def _run_loop() -> None:
    from fastapi.concurrency import run_in_threadpool

    from app.services.monitoring import process_one_due_monitor

    while True:
        try:
            # Blocking (network + parsing) — keep it off the event loop so it
            # can't stall request handling.
            summary = await run_in_threadpool(process_one_due_monitor)
            if summary:
                logger.info(
                    "[monitor] checked %s -> %s%s",
                    summary.get("monitorId"),
                    summary.get("status"),
                    " (alert sent)" if summary.get("alerted") else "",
                )
        except Exception as exc:  # pragma: no cover - the loop must never die
            logger.exception("[monitor] tick raised: %s", exc)
        await asyncio.sleep(_TICK_SECONDS)


def schedule_monitor_task() -> None:
    """Start the monitor loop on the running event loop. Idempotent."""
    global _started
    if _started:
        return
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return  # no loop yet; the startup hook calls us again
    loop.create_task(_run_loop())
    _started = True
    logger.info("[monitor] scheduled site-monitor checks every %ds", _TICK_SECONDS)


__all__ = ["schedule_monitor_task"]
