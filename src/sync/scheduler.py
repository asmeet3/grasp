"""Scheduler — runs sync via APScheduler BackgroundScheduler.

Configurable cron triggers for working-hours sync, manual trigger support,
and graceful shutdown.
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
    """Manages the sync schedule using APScheduler."""

    def __init__(
        self,
        orchestrator: SyncOrchestrator,
        hours: list[int] | None = None,
        minute: int = 0,
    ):
        self.orchestrator = orchestrator
        self.hours = hours or [8, 11, 14, 17, 20]
        self.minute = minute
        self.scheduler = BackgroundScheduler()
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self, loop: asyncio.AbstractEventLoop | None = None):
        """Start the background scheduler."""
        self._loop = loop

        # Comma-separated hours for a single cron trigger
        hours_str = ",".join(str(h) for h in sorted(self.hours))
        self.scheduler.add_job(
            self._trigger_sync,
            trigger=CronTrigger(hour=hours_str, minute=self.minute),
            id="working_hours_sync",
            name="Working Hours Knowledge Sync",
            replace_existing=True,
        )

        self.scheduler.start()
        formatted = ", ".join(f"{h:02d}:{self.minute:02d}" for h in sorted(self.hours))
        logger.info(f"Sync scheduler started — runs at {formatted} UTC")

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
        job = self.scheduler.get_job("working_hours_sync")
        if job and job.next_run_time:
            return job.next_run_time.isoformat()
        return None
