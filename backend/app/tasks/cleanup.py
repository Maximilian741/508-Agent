"""Periodic cleanup of expired pipeline artifacts.

Runs as a single ``asyncio`` task scheduled at app startup.  It walks
``settings.materialized_root / "pipeline"`` once an hour and deletes any
job folder whose mtime is older than ``settings.pipeline_artifact_ttl_seconds``.

Best-effort by design: a permission error or a corrupt path logs and
continues.  We never abort the loop on a single bad job.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

_SWEEP_INTERVAL_SECONDS = 60 * 60  # 1 hour
_started = False


def _sweep_pipeline_root(root: Path, ttl_seconds: int) -> int:
    """Delete job folders older than ``ttl_seconds``.  Returns count removed."""

    if not root.exists():
        return 0
    cutoff = time.time() - max(60, ttl_seconds)
    removed = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            if child.stat().st_mtime > cutoff:
                continue
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
        except Exception as exc:  # pragma: no cover - logging only
            logger.warning("[cleanup] failed to remove %s: %s", child, exc)
    return removed


async def _sweep_loop() -> None:
    settings = get_settings()
    pipeline_root = settings.materialized_root / "pipeline"
    while True:
        try:
            removed = _sweep_pipeline_root(
                pipeline_root, settings.pipeline_artifact_ttl_seconds
            )
            if removed:
                logger.info(
                    "[cleanup] removed %d expired pipeline artifact folder(s) under %s",
                    removed,
                    pipeline_root,
                )
        except Exception as exc:  # pragma: no cover
            logger.exception("[cleanup] sweep raised: %s", exc)
        await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)


def schedule_cleanup_task() -> None:
    """Schedule the periodic sweep on the running event loop.

    Idempotent — second/third calls are no-ops so it's safe to invoke from
    multiple startup hooks (FastAPI startup, dev launcher, etc.).
    """

    global _started
    if _started:
        return
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        # No loop yet; FastAPI's startup hook will call us again once one is
        # running.
        return
    loop.create_task(_sweep_loop())
    _started = True
    logger.info("[cleanup] scheduled pipeline-artifact sweep every %ds", _SWEEP_INTERVAL_SECONDS)


__all__ = ["schedule_cleanup_task"]
