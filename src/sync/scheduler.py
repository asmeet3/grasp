"""Scheduler — runs daily sync via APScheduler BackgroundScheduler.

Configurable cron trigger, manual trigger support, and graceful shutdown.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

if TYPE_CHECKING:
    from .orchestrator import SyncOrchestrator

logger = logging.getLogger(__name__)


class SyncScheduler:
    """Manages the daily sync schedule using APScheduler."""

    def __init__(self, orchestrator: SyncOrchestrator, hour: int = 2, minute: int = 0):
        self.orchestrator = orchestrator
        self.hour = hour
        self.minute = minute
        self.scheduler = BackgroundScheduler()
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self, loop: asyncio.AbstractEventLoop | None = None):
        """Start the background scheduler."""
        self._loop = loop

        self.scheduler.add_job(
            self._trigger_sync,
            trigger=CronTrigger(hour=self.hour, minute=self.minute),
            id="daily_sync",
            name="Daily Knowledge Sync",
            replace_existing=True,
        )

        self.scheduler.start()
        logger.info(f"Sync scheduler started — daily at {self.hour:02d}:{self.minute:02d} UTC")

    def stop(self):
        """Stop the scheduler gracefully."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Sync scheduler stopped")

    def trigger_now(self):
        """Manually trigger a sync run."""
        logger.info("Manual sync triggered")
        self._trigger_sync()

    def _trigger_sync(self):
        """Internal: run the sync in the event loop."""
        if self.orchestrator.is_running:
            logger.warning("Sync already in progress, skipping scheduled trigger")
            return

        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.orchestrator.run_sync(), self._loop
            )
        else:
            # Fallback: create a new event loop
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.orchestrator.run_sync())
            finally:
                loop.close()

    @property
    def next_run_time(self) -> str | None:
        """Get the next scheduled run time."""
        job = self.scheduler.get_job("daily_sync")
        if job and job.next_run_time:
            return job.next_run_time.isoformat()
        return None
