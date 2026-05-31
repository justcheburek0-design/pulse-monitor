"""APScheduler-based monitor check scheduler."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.config.database import async_session_factory
from src.models.monitor import Monitor, MonitorStatus
from src.workers.check_worker import CheckExecutor
from src.services.monitor_service import MonitorService
from src.services.alert_service import AlertService

logger = logging.getLogger("pulse.scheduler")
settings = get_settings()


class MonitorScheduler:
    """Schedules and runs monitor checks."""

    def __init__(self):
        self.scheduler = AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            job_defaults={
                "coalesce": settings.scheduler_coalesce,
                "max_instances": settings.scheduler_max_instances,
            },
        )
        self.executor = CheckExecutor()
        self._running = False

    async def start(self):
        """Start the scheduler."""
        if not settings.scheduler_enabled:
            logger.warning("Scheduler is disabled in settings")
            return

        # Load all active monitors and schedule them
        async with async_session_factory() as session:
            monitors = await MonitorService.get_monitors_to_check(session)
            for monitor in monitors:
                self._schedule_monitor(monitor)
            logger.info(f"Scheduled {len(monitors)} monitors")

        self.scheduler.start()
        self._running = True
        logger.info("Scheduler started")

    async def stop(self):
        """Stop the scheduler."""
        if self._running:
            self.scheduler.shutdown(wait=False)
            self._running = False
            logger.info("Scheduler stopped")

    def _schedule_monitor(self, monitor: Monitor):
        """Add a monitor to the scheduler."""
        job_id = f"monitor_{monitor.id}"
        existing = self.scheduler.get_job(job_id)

        if existing:
            if not monitor.is_active or monitor.status == MonitorStatus.PAUSED:
                self.scheduler.remove_job(job_id)
                logger.info(f"Unscheduled monitor {monitor.name}")
                return
            # Reschedule if interval changed
            existing.reschedule(
                trigger=IntervalTrigger(seconds=monitor.interval_seconds)
            )
            return

        if not monitor.is_active or monitor.status == MonitorStatus.PAUSED:
            return

        self.scheduler.add_job(
            self._run_check,
            trigger=IntervalTrigger(seconds=monitor.interval_seconds),
            id=job_id,
            args=[monitor.id],
            name=f"Check: {monitor.name}",
            replace_existing=True,
        )
        logger.info(f"Scheduled monitor {monitor.name} (every {monitor.interval_seconds}s)")

    async def _run_check(self, monitor_id: str):
        """Execute a single monitor check and process results."""
        async with async_session_factory() as session:
            monitor = await session.get(Monitor, monitor_id)
            if not monitor or not monitor.is_active:
                return

            logger.debug(f"Running check for {monitor.name}")
            result = await self.executor.execute(monitor)

            # Record check result
            check = await MonitorService.record_check(
                db=session,
                monitor_id=monitor.id,
                is_up=result.is_up,
                response_time_ms=result.response_time_ms,
                status_code=result.status_code,
                error_message=result.error_message,
                dns_ms=result.dns_resolution_ms,
                tls_ms=result.tls_handshake_ms,
                ttfb_ms=result.ttfb_ms,
                content_length=result.content_length,
            )

            # Process alerts
            if not result.is_up:
                logger.warning(f"Monitor {monitor.name} is DOWN: {result.error_message}")
                await AlertService.process_down_alert(session, monitor, check)
            else:
                await AlertService.process_recovery_alert(session, monitor, check)

            await session.commit()

    def get_status(self) -> dict:
        """Get scheduler status."""
        jobs = self.scheduler.get_jobs()
        return {
            "running": self._running,
            "scheduled_jobs": len(jobs),
            "jobs": [
                {"id": j.id, "name": j.name, "next_run": str(j.next_run_time)}
                for j in jobs
            ],
        }
