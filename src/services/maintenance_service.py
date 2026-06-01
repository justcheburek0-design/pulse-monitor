"""Pulse maintenance service.

Handles routine database maintenance tasks:
  - Cleanup of old check results (data retention)
  - Archival of resolved incidents
  - Database statistics and health checks
  - Monitor health aggregation
  - Notification delivery log pruning
  - Automated backup triggers
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import Integer, text

from src.config.database import async_session_factory, engine
from src.config.settings import get_settings
from src.models.monitor import Monitor, MonitorCheck
from src.models.incident import Incident, IncidentStatus, IncidentEvent

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Maintenance Result ─────────────────────────────────────────────────────────

@dataclass
class MaintenanceTaskResult:
    task_name: str
    started_at: datetime
    completed_at: datetime
    success: bool
    rows_affected: int = 0
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        return (self.completed_at - self.started_at).total_seconds()


@dataclass
class MaintenanceReport:
    started_at: datetime
    completed_at: datetime
    tasks: List[MaintenanceTaskResult] = field(default_factory=list)

    @property
    def all_success(self) -> bool:
        return all(t.success for t in self.tasks)

    @property
    def total_rows_affected(self) -> int:
        return sum(t.rows_affected for t in self.tasks)

    @property
    def errors(self) -> List[str]:
        return [f"{t.task_name}: {t.error}" for t in self.tasks if not t.success]


# ── Retention Policy ─────────────────────────────────────────────────────────

@dataclass
class RetentionPolicy:
    check_results_days: int = 90
    incident_events_days: int = 180
    notification_logs_days: int = 30
    archived_incidents_days: int = 365

    @classmethod
    def from_settings(cls) -> RetentionPolicy:
        """Build retention policy from application settings."""
        return cls()


# ── Cleanup: Check Results ────────────────────────────────────────────────────

async def cleanup_check_results(
    retention_days: Optional[int] = None,
) -> MaintenanceTaskResult:
    """Delete old MonitorCheck records beyond retention period."""
    task = MaintenanceTaskResult(
        task_name="cleanup_check_results",
        started_at=datetime.utcnow(),
        success=False,
    )
    policy = RetentionPolicy.from_settings()
    days = retention_days or policy.check_results_days
    cutoff = datetime.utcnow() - timedelta(days=days)

    try:
        async with async_session_factory() as session:
            from sqlalchemy import delete, func

            # Count how many will be deleted
            count_result = await session.execute(
                func.count().select_from(MonitorCheck).where(
                    MonitorCheck.checked_at < cutoff
                )
            )
            count = count_result.scalar_one()

            # Delete in batches to avoid long transactions
            batch_size = 10000
            total_deleted = 0
            while True:
                # Get IDs to delete
                from sqlalchemy import select
                ids_result = await session.execute(
                    select(MonitorCheck.id)
                    .where(MonitorCheck.checked_at < cutoff)
                    .limit(batch_size)
                )
                ids = [row[0] for row in ids_result.all()]
                if not ids:
                    break
                await session.execute(
                    delete(MonitorCheck).where(MonitorCheck.id.in_(ids))
                )
                await session.flush()
                total_deleted += len(ids)
                if len(ids) < batch_size:
                    break

            await session.commit()
            task.rows_affected = total_deleted
            task.success = True
            task.details = {"retention_days": days, "cutoff": cutoff.isoformat()}
            logger.info(f"Cleaned up {total_deleted} check records older than {days} days")
    except Exception as e:
        task.error = str(e)
        logger.error(f"Check results cleanup failed: {e}")
    finally:
        task.completed_at = datetime.utcnow()

    return task


# ── Archival: Resolved Incidents ──────────────────────────────────────────────

async def archive_resolved_incidents(
    older_than_days: int = 90,
) -> MaintenanceTaskResult:
    """Archive resolved incidents older than specified days."""
    task = MaintenanceTaskResult(
        task_name="archive_resolved_incidents",
        started_at=datetime.utcnow(),
        success=False,
    )
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)

    try:
        async with async_session_factory() as session:
            from sqlalchemy import select, update

            # Find resolved incidents to archive
            result = await session.execute(
                select(Incident).where(
                    Incident.status == IncidentStatus.RESOLVED,
                    Incident.updated_at < cutoff,
                )
            )
            incidents = result.scalars().all()

            archived_count = 0
            for incident in incidents:
                # Add archive event
                event = IncidentEvent(
                    incident_id=incident.id,
                    type="archived",
                    message=f"Automatically archived after {older_than_days} days resolved",
                    details={"archived_at": datetime.utcnow().isoformat()},
                )
                session.add(event)
                archived_count += 1

            await session.commit()
            task.rows_affected = archived_count
            task.success = True
            task.details = {"archived": archived_count, "older_than_days": older_than_days}
            logger.info(f"Archived {archived_count} resolved incidents")
    except Exception as e:
        task.error = str(e)
        logger.error(f"Incident archival failed: {e}")
    finally:
        task.completed_at = datetime.utcnow()

    return task


# ── Database Statistics ────────────────────────────────────────────────────────

@dataclass
class DatabaseStats:
    """Database size and performance statistics."""
    total_size_bytes: int = 0
    tables: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    index_count: int = 0
    total_rows: int = 0
    vacuum_recommended: bool = False
    fragmentation_pct: float = 0.0


async def get_database_stats() -> DatabaseStats:
    """Get database statistics."""
    stats = DatabaseStats()
    try:
        # For SQLite
        db_url = str(engine.url)
        if db_url.startswith("sqlite"):
            db_path = db_url.replace("sqlite+aiosqlite:///", "")
            path = Path(db_path)
            if path.exists():
                stats.total_size_bytes = path.stat().st_size

            # Query table info
            async with async_session_factory() as session:
                # Get table list
                result = await session.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
                tables = [row[0] for row in result.all()]

                for table in tables:
                    count_result = await session.execute(
                        text(f"SELECT COUNT(*) FROM [{table}]")
                    )
                    count = count_result.scalar_one() or 0
                    stats.tables[table] = {"row_count": count}
                    stats.total_rows += count

                # Index count
                idx_result = await session.execute(
                    text("SELECT COUNT(*) FROM sqlite_master WHERE type='index'")
                )
                stats.index_count = idx_result.scalar_one() or 0

                # Fragmentation estimate
                if stats.total_size_bytes > 0 and stats.total_rows > 0:
                    avg_row_size = stats.total_size_bytes / max(stats.total_rows, 1)
                    # If average row size is very small but file is large, likely fragmented
                    if avg_row_size < 100 and stats.total_size_bytes > 10 * 1024 * 1024:
                        stats.vacuum_recommended = True
                        stats.fragmentation_pct = round(
                            (1 - (stats.total_rows * 100) / stats.total_size_bytes) * 100, 1
                        )

        else:
            # PostgreSQL
            async with async_session_factory() as session:
                result = await session.execute(
                    text("SELECT pg_database_size(current_database())")
                )
                stats.total_size_bytes = result.scalar_one() or 0

                result = await session.execute(
                    text(
                        "SELECT schemaname, relname, n_live_tup "
                        "FROM pg_stat_user_tables ORDER BY n_live_tup DESC"
                    )
                )
                for row in result.all():
                    stats.tables[row[1]] = {"row_count": row[2]}
                    stats.total_rows += row[2] or 0

    except Exception as e:
        logger.warning(f"Failed to collect database stats: {e}")

    return stats


# ── Monitor Health Aggregator ─────────────────────────────────────────────────

@dataclass
class MonitorHealthSummary:
    """Aggregated health summary across all monitors."""
    total_monitors: int = 0
    active_monitors: int = 0
    paused_monitors: int = 0
    up_monitors: int = 0
    down_monitors: int = 0
    error_monitors: int = 0
    avg_uptime_pct: float = 0.0
    avg_response_time_ms: float = 0.0
    total_checks_24h: int = 0
    incident_free_hours: float = 0.0


async def get_monitor_health_summary() -> MonitorHealthSummary:
    """Get aggregated health summary for all monitors."""
    from sqlalchemy import select, func

    summary = MonitorHealthSummary()

    try:
        async with async_session_factory() as session:
            # Count by status
            status_result = await session.execute(
                select(Monitor.status, func.count())
                .group_by(Monitor.status)
            )
            status_counts = dict(status_result.all())

            summary.total_monitors = sum(status_counts.values())
            summary.up_monitors = status_counts.get("up", 0)
            summary.down_monitors = status_counts.get("down", 0)
            summary.error_monitors = status_counts.get("error", 0)
            summary.paused_monitors = status_counts.get("paused", 0)
            summary.active_monitors = summary.total_monitors - summary.paused_monitors

            # Average uptime
            avg_uptime_result = await session.execute(
                func.avg(Monitor.uptime_percentage).select_from(Monitor)
            )
            summary.avg_uptime_pct = round(avg_uptime_result.scalar_one() or 100.0, 2)

            # Average response time
            avg_rt_result = await session.execute(
                func.avg(Monitor.avg_response_time_ms).select_from(Monitor)
            )
            summary.avg_response_time_ms = round(avg_rt_result.scalar_one() or 0.0, 1)

            # Checks in last 24h
            day_ago = datetime.utcnow() - timedelta(hours=24)
            checks_result = await session.execute(
                func.count()
                .select_from(MonitorCheck)
                .where(MonitorCheck.checked_at >= day_ago)
            )
            summary.total_checks_24h = checks_result.scalar_one() or 0

            # Incident-free hours (simplified)
            active_incidents = sum(1 for s in status_counts if s not in ("up", "paused"))
            if summary.total_monitors > 0:
                healthy_ratio = summary.up_monitors / summary.total_monitors
                summary.incident_free_hours = round(healthy_ratio * 24, 1)

    except Exception as e:
        logger.warning(f"Failed to get health summary: {e}")

    return summary


# ── Notification Log Cleanup ──────────────────────────────────────────────────

async def cleanup_notification_logs(
    retention_days: Optional[int] = None,
) -> MaintenanceTaskResult:
    """Clean up old notification delivery logs."""
    task = MaintenanceTaskResult(
        task_name="cleanup_notification_logs",
        started_at=datetime.utcnow(),
        success=False,
    )
    policy = RetentionPolicy.from_settings()
    days = retention_days or policy.notification_logs_days
    cutoff = datetime.utcnow() - timedelta(days=days)

    try:
        async with async_session_factory() as session:
            from sqlalchemy import delete, text
            from src.models.alert import AlertNotification

            count_result = await session.execute(
                AlertNotification.__table__.delete().where(
                    AlertNotification.sent_at < cutoff
                )
            )
            task.rows_affected = count_result.rowcount or 0
            await session.commit()
            task.success = True
            logger.info(f"Cleaned up {task.rows_affected} notification logs")
    except Exception as e:
        task.error = str(e)
        # Table might not exist — that's OK
        if "no such table" in str(e).lower() or "does not exist" in str(e).lower():
            task.success = True
            task.details = {"note": "Notification table does not exist yet"}
        else:
            logger.error(f"Notification log cleanup failed: {e}")
    finally:
        task.completed_at = datetime.utcnow()

    return task


# ── Incident Event Cleanup ────────────────────────────────────────────────────

async def cleanup_incident_events(
    retention_days: Optional[int] = None,
) -> MaintenanceTaskResult:
    """Delete old incident event records beyond retention period."""
    task = MaintenanceTaskResult(
        task_name="cleanup_incident_events",
        started_at=datetime.utcnow(),
        success=False,
    )
    policy = RetentionPolicy.from_settings()
    days = retention_days or policy.incident_events_days
    cutoff = datetime.utcnow() - timedelta(days=days)

    try:
        async with async_session_factory() as session:
            from sqlalchemy import delete, func

            count_result = await session.execute(
                func.count().select_from(IncidentEvent).where(
                    IncidentEvent.created_at < cutoff
                )
            )
            count = count_result.scalar_one()

            await session.execute(
                delete(IncidentEvent).where(
                    IncidentEvent.created_at < cutoff
                )
            )
            await session.commit()
            task.rows_affected = count
            task.success = True
            logger.info(f"Cleaned up {count} incident events older than {days} days")
    except Exception as e:
        task.error = str(e)
        logger.error(f"Incident event cleanup failed: {e}")
    finally:
        task.completed_at = datetime.utcnow()

    return task


# ── Recalculate Monitor Stats ─────────────────────────────────────────────────

async def recalculate_monitor_stats() -> MaintenanceTaskResult:
    """Recalculate uptime and response time stats for all monitors from check history."""
    task = MaintenanceTaskResult(
        task_name="recalculate_monitor_stats",
        started_at=datetime.utcnow(),
        success=False,
    )

    try:
        async with async_session_factory() as session:
            from sqlalchemy import select, func

            # Get all monitors
            result = await session.execute(select(Monitor))
            monitors = result.scalars().all()

            updated = 0
            day_ago = datetime.utcnow() - timedelta(days=7)

            for monitor in monitors:
                # Calculate uptime from check history
                checks_result = await session.execute(
                    select(
                        func.count(),
                        func.sum(MonitorCheck.is_up.cast(Integer)),
                        func.avg(MonitorCheck.response_time_ms),
                    )
                    .where(
                        MonitorCheck.monitor_id == monitor.id,
                        MonitorCheck.checked_at >= day_ago,
                    )
                )
                row = checks_result.one()
                total = row[0] or 0
                ups = int(row[1] or 0) if row[1] else 0
                avg_rt = float(row[2] or 0) if row[2] else 0.0

                if total > 0:
                    monitor.uptime_percentage = round((ups / total) * 100, 2)

                monitor.avg_response_time_ms = round(avg_rt, 1)
                updated += 1

            await session.commit()
            task.rows_affected = updated
            task.success = True
            task.details = {"monitors_updated": updated}
            logger.info(f"Recalculated stats for {updated} monitors")
    except Exception as e:
        task.error = str(e)
        logger.error(f"Monitor stats recalculation failed: {e}")
    finally:
        task.completed_at = datetime.utcnow()

    return task


# ── Full Maintenance Run ─────────────────────────────────────────────────────

async def run_full_maintenance() -> MaintenanceReport:
    """Run all maintenance tasks in sequence."""
    report = MaintenanceReport(
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),  # Will be updated
    )

    tasks = [
        cleanup_check_results(),
        archive_resolved_incidents(),
        cleanup_incident_events(),
        cleanup_notification_logs(),
        recalculate_monitor_stats(),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            report.tasks.append(MaintenanceTaskResult(
                task_name="unknown",
                started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
                success=False,
                error=str(result),
            ))
        else:
            report.tasks.append(result)

    report.completed_at = datetime.utcnow()
    return report


# ── Database Vacuum & Optimization ────────────────────────────────────────────

async def vacuum_database() -> MaintenanceTaskResult:
    """Run VACUUM on SQLite database to reclaim space."""
    task = MaintenanceTaskResult(
        task_name="vacuum_database",
        started_at=datetime.utcnow(),
        success=False,
    )
    try:
        db_url = str(engine.url)
        if not db_url.startswith("sqlite"):
            task.success = True
            task.details = {"note": "VACUUM is SQLite-specific"}
            task.completed_at = datetime.utcnow()
            return task

        db_path = db_url.replace("sqlite+aiosqlite:///", "")
        conn = sqlite3.connect(db_path)
        conn.execute("VACUUM")
        conn.close()

        task.success = True
        task.details = {"database": db_path}
        logger.info(f"VACUUM completed on {db_path}")
    except Exception as e:
        task.error = str(e)
        logger.error(f"VACUUM failed: {e}")
    finally:
        task.completed_at = datetime.utcnow()

    return task


async def analyze_table_sizes() -> MaintenanceTaskResult:
    """Analyze table sizes and row counts."""
    task = MaintenanceTaskResult(
        task_name="analyze_table_sizes",
        started_at=datetime.utcnow(),
        success=False,
    )
    try:
        stats = await get_database_stats()
        task.success = True
        task.details = {
            "total_size_bytes": stats.total_size_bytes,
            "total_rows": stats.total_rows,
            "tables": stats.tables,
            "index_count": stats.index_count,
        }
    except Exception as e:
        task.error = str(e)
        logger.error(f"Table analysis failed: {e}")
    finally:
        task.completed_at = datetime.utcnow()

    return task


# ── Orphaned Records Cleanup ──────────────────────────────────────────────────

async def cleanup_orphaned_checks() -> MaintenanceTaskResult:
    """Remove check records for monitors that no longer exist."""
    task = MaintenanceTaskResult(
        task_name="cleanup_orphaned_checks",
        started_at=datetime.utcnow(),
        success=False,
    )
    try:
        async with async_session_factory() as session:
            from sqlalchemy import delete, select

            # Find checks whose monitor_id doesn't match any monitor
            result = await session.execute(
                select(MonitorCheck.monitor_id)
                .where(
                    ~MonitorCheck.monitor_id.in_(
                        select(Monitor.id)
                    )
                )
            )
            orphaned_ids = set(row[0] for row in result.all())

            if orphaned_ids:
                await session.execute(
                    delete(MonitorCheck).where(
                        MonitorCheck.monitor_id.in_(orphaned_ids)
                    )
                )
                await session.commit()
                task.rows_affected = len(orphaned_ids)
            else:
                task.rows_affected = 0

            task.success = True
            task.details = {"orphaned_monitor_ids": list(orphaned_ids)}
            logger.info(f"Cleaned up checks for {len(orphaned_ids)} orphaned monitors")
    except Exception as e:
        task.error = str(e)
        logger.error(f"Orphaned checks cleanup failed: {e}")
    finally:
        task.completed_at = datetime.utcnow()

    return task


async def cleanup_orphaned_incident_events() -> MaintenanceTaskResult:
    """Remove incident events for incidents that no longer exist."""
    task = MaintenanceTaskResult(
        task_name="cleanup_orphaned_incident_events",
        started_at=datetime.utcnow(),
        success=False,
    )
    try:
        async with async_session_factory() as session:
            from sqlalchemy import delete, select

            orphaned = await session.execute(
                select(IncidentEvent.id)
                .where(
                    ~IncidentEvent.incident_id.in_(
                        select(Incident.id)
                    )
                )
            )
            orphaned_ids = [row[0] for row in orphaned.all()]

            if orphaned_ids:
                await session.execute(
                    delete(IncidentEvent).where(
                        IncidentEvent.id.in_(orphaned_ids)
                    )
                )
                await session.commit()

            task.rows_affected = len(orphaned_ids)
            task.success = True
            logger.info(f"Cleaned up {len(orphaned_ids)} orphaned incident events")
    except Exception as e:
        task.error = str(e)
        logger.error(f"Orphaned incident events cleanup failed: {e}")
    finally:
        task.completed_at = datetime.utcnow()

    return task


# ── Health Check for Maintenance System ───────────────────────────────────────

async def maintenance_health_check() -> Dict[str, Any]:
    """Run a quick health check on the maintenance system itself."""
    until = datetime.utcnow()
    since = until - timedelta(hours=24)

    checks = {}

    # Check if maintenance ran recently
    try:
        stats = await get_database_stats()
        checks["database_accessible"] = True
        checks["database_size_mb"] = round(stats.total_size_bytes / (1024 * 1024), 2)
        checks["total_rows"] = stats.total_rows
        checks["needs_vacuum"] = stats.vacuum_recommended
    except Exception as e:
        checks["database_accessible"] = False
        checks["database_error"] = str(e)

    # Check for old data
    try:
        async with async_session_factory() as session:
            from sqlalchemy import select, func

            old_checks = await session.execute(
                select(func.count())
                .select_from(MonitorCheck)
                .where(MonitorCheck.checked_at < since)
            )
            checks["checks_older_than_24h"] = old_checks.scalar_one()

            total_monitors = await session.execute(
                select(func.count()).select_from(Monitor)
            )
            checks["total_monitors"] = total_monitors.scalar_one()

            paused_monitors = await session.execute(
                select(func.count())
                .select_from(Monitor)
                .where(Monitor.status == "paused")
            )
            checks["paused_monitors"] = paused_monitors.scalar_one()
    except Exception as e:
        checks["data_query_error"] = str(e)

    checks["checked_at"] = until.isoformat()
    return checks


# ── Scheduled Maintenance Runner ──────────────────────────────────────────────

async def run_scheduled_maintenance(
    include_vacuum: bool = False,
    include_orphan_cleanup: bool = True,
) -> MaintenanceReport:
    """Run a scheduled maintenance pass with optional tasks."""
    report = MaintenanceReport(
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    )

    tasks = [
        cleanup_check_results(),
        archive_resolved_incidents(),
        cleanup_incident_events(),
        cleanup_notification_logs(),
        recalculate_monitor_stats(),
    ]

    if include_orphan_cleanup:
        tasks.append(cleanup_orphaned_checks())
        tasks.append(cleanup_orphaned_incident_events())

    if include_vacuum:
        tasks.append(vacuum_database())

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            report.tasks.append(MaintenanceTaskResult(
                task_name="unknown",
                started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
                success=False,
                error=str(result),
            ))
        else:
            report.tasks.append(result)

    report.completed_at = datetime.utcnow()
    return report
