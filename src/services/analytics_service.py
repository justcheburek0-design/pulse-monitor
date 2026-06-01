"""Pulse analytics service.

Provides uptime calculations, response time statistics, incident frequency
analysis, SLA compliance tracking, and report generation (CSV, JSON, PDF-ready).
"""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from src.config.database import async_session_factory
from src.models.monitor import Monitor, MonitorCheck

logger = logging.getLogger(__name__)


# ── Time Periods ───────────────────────────────────────────────────────────────

class TimePeriod(Enum):
    HOUR_1 = "1h"
    HOURS_6 = "6h"
    HOURS_24 = "24h"
    DAYS_7 = "7d"
    DAYS_30 = "30d"
    DAYS_90 = "90d"


def period_to_timedelta(period: TimePeriod) -> timedelta:
    mapping = {
        TimePeriod.HOUR_1: timedelta(hours=1),
        TimePeriod.HOURS_6: timedelta(hours=6),
        TimePeriod.HOURS_24: timedelta(hours=24),
        TimePeriod.DAYS_7: timedelta(days=7),
        TimePeriod.DAYS_30: timedelta(days=30),
        TimePeriod.DAYS_90: timedelta(days=90),
    }
    return mapping.get(period, timedelta(hours=24))


# ── Data Classes ───────────────────────────────────────────────────────────────

@dataclass
class PercentileStats:
    p50: float = 0.0
    p75: float = 0.0
    p90: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    avg: float = 0.0
    min: float = 0.0
    max: float = 0.0
    count: int = 0


@dataclass
class UptimeRecord:
    period: str
    total_checks: int
    up_checks: int
    down_checks: int
    uptime_pct: float
    downtime_minutes: float
    start_time: datetime
    end_time: datetime


@dataclass
class IncidentSummary:
    total_incidents: int
    resolved_incidents: int
    active_incidents: int
    avg_resolution_minutes: float
    mttr_minutes: float  # Mean Time To Resolve
    by_severity: Dict[str, int]
    by_status: Dict[str, int]
    most_affected_monitors: List[Dict[str, Any]]


@dataclass
class MonitorAnalytics:
    monitor_id: str
    monitor_name: str
    period: str
    uptime: UptimeRecord
    response_time: PercentileStats
    incidents: IncidentSummary
    total_checks: int
    ssl_expiry_days: Optional[int] = None


@dataclass
class SLAReport:
    period: str
    target_uptime_pct: float
    actual_uptime_pct: float
    is_compliant: bool
    downtime_minutes: float
    allowed_downtime_minutes: float
    breach_count: int
    credits_due: float  # percentage of monthly fee


@dataclass
class TrendDataPoint:
    timestamp: datetime
    value: float
    label: str = ""


@dataclass
class TrendAnalysis:
    metric_name: str
    period: str
    data_points: List[TrendDataPoint]
    trend_direction: str  # "improving", "degrading", "stable"
    trend_pct: float  # percentage change over period
    predicted_next_value: Optional[float] = None
    anomaly_points: List[TrendDataPoint] = field(default_factory=list)


# ── Percentile Calculator ─────────────────────────────────────────────────────

def calculate_percentiles(values: List[float]) -> PercentileStats:
    """Calculate percentile statistics from a list of values."""
    if not values:
        return PercentileStats()

    sorted_vals = sorted(values)
    n = len(sorted_vals)

    def percentile(pct: float) -> float:
        idx = int(pct / 100.0 * (n - 1))
        idx = max(0, min(idx, n - 1))
        lower = int(idx)
        upper = min(lower + 1, n - 1)
        frac = idx - lower
        return sorted_vals[lower] + frac * (sorted_vals[upper] - sorted_vals[lower])

    return PercentileStats(
        p50=round(percentile(50), 2),
        p75=round(percentile(75), 2),
        p90=round(percentile(90), 2),
        p95=round(percentile(95), 2),
        p99=round(percentile(99), 2),
        avg=round(sum(sorted_vals) / n, 2),
        min=round(sorted_vals[0], 2),
        max=round(sorted_vals[-1], 2),
        count=n,
    )


# ── Uptime Calculator ─────────────────────────────────────────────────────────

async def calculate_uptime(
    monitor_id: str,
    since: datetime,
    until: Optional[datetime] = None,
) -> UptimeRecord:
    """Calculate uptime for a monitor over a time period."""
    from sqlalchemy import select, func

    until = until or datetime.utcnow()
    period_str = f"{since.isoformat()} to {until.isoformat()}"

    async with async_session_factory() as session:
        total_result = await session.execute(
            select(func.count())
            .select_from(MonitorCheck)
            .where(
                MonitorCheck.monitor_id == monitor_id,
                MonitorCheck.checked_at >= since,
                MonitorCheck.checked_at <= until,
            )
        )
        total = total_result.scalar_one()

        up_result = await session.execute(
            select(func.count())
            .select_from(MonitorCheck)
            .where(
                MonitorCheck.monitor_id == monitor_id,
                MonitorCheck.checked_at >= since,
                MonitorCheck.checked_at <= until,
                MonitorCheck.is_up == True,
            )
        )
        up = up_result.scalar_one()

        down = total - up
        uptime_pct = round((up / total) * 100, 4) if total > 0 else 100.0

        if total > 0 and down > 0:
            period_minutes = (until - since).total_seconds() / 60
            downtime_minutes = round((down / total) * period_minutes, 1)
        else:
            downtime_minutes = 0.0

        return UptimeRecord(
            period=period_str,
            total_checks=total,
            up_checks=up,
            down_checks=down,
            uptime_pct=uptime_pct,
            downtime_minutes=downtime_minutes,
            start_time=since,
            end_time=until,
        )


async def calculate_response_times(
    monitor_id: str,
    since: datetime,
    until: Optional[datetime] = None,
) -> PercentileStats:
    """Calculate response time percentiles for a monitor."""
    from sqlalchemy import select

    until = until or datetime.utcnow()

    async with async_session_factory() as session:
        result = await session.execute(
            select(MonitorCheck.response_time_ms)
            .where(
                MonitorCheck.monitor_id == monitor_id,
                MonitorCheck.checked_at >= since,
                MonitorCheck.checked_at <= until,
                MonitorCheck.is_up == True,
                MonitorCheck.response_time_ms > 0,
            )
            .order_by(MonitorCheck.response_time_ms)
        )
        values = [row[0] for row in result.all()]

    return calculate_percentiles(values)


async def calculate_incident_summary(
    monitor_id: str,
    since: datetime,
    until: Optional[datetime] = None,
) -> IncidentSummary:
    """Calculate incident summary for a monitor."""
    from sqlalchemy import select, func
    from src.models.incident import Incident, IncidentStatus, IncidentSeverity

    until = until or datetime.utcnow()

    async with async_session_factory() as session:
        total_result = await session.execute(
            select(func.count())
            .select_from(Incident)
            .where(
                Incident.created_at >= since,
                Incident.created_at <= until,
            )
        )
        total = total_result.scalar_one()

        resolved_result = await session.execute(
            select(func.count())
            .select_from(Incident)
            .where(
                Incident.created_at >= since,
                Incident.created_at <= until,
                Incident.status == IncidentStatus.RESOLVED,
            )
        )
        resolved = resolved_result.scalar_one()

        active_result = await session.execute(
            select(func.count())
            .select_from(Incident)
            .where(Incident.status != IncidentStatus.RESOLVED)
        )
        active = active_result.scalar_one()

        resolution_result = await session.execute(
            select(
                Incident.created_at,
                Incident.updated_at,
                Incident.severity,
                Incident.status,
            )
            .where(
                Incident.created_at >= since,
                Incident.created_at <= until,
                Incident.status == IncidentStatus.RESOLVED,
            )
        )
        resolution_rows = resolution_result.all()

    resolution_minutes = []
    for row in resolution_rows:
        if row[1] and row[0]:
            mins = (row[1] - row[0]).total_seconds() / 60
            resolution_minutes.append(mins)

    avg_resolution = round(sum(resolution_minutes) / len(resolution_minutes), 1) if resolution_minutes else 0.0

    by_severity: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for row in resolution_rows:
        sev = str(row[2]) if len(row) > 2 else "unknown"
        stat = str(row[3]) if len(row) > 3 else "unknown"
        by_severity[sev] = by_severity.get(sev, 0) + 1
        by_status[stat] = by_status.get(stat, 0) + 1

    return IncidentSummary(
        total_incidents=total,
        resolved_incidents=resolved,
        active_incidents=active,
        avg_resolution_minutes=avg_resolution,
        mttr_minutes=avg_resolution,
        by_severity=by_severity,
        by_status=by_status,
        most_affected_monitors=[],
    )


async def generate_sla_report(
    monitor_id: str,
    period: TimePeriod = TimePeriod.DAYS_30,
    target_uptime_pct: float = 99.9,
) -> SLAReport:
    """Generate SLA compliance report for a monitor."""
    until = datetime.utcnow()
    since = until - period_to_timedelta(period)

    uptime = await calculate_uptime(monitor_id, since, until)

    period_days = (until - since).days or 30
    total_period_minutes = period_days * 24 * 60
    allowed_downtime = round(
        total_period_minutes * (1 - target_uptime_pct / 100), 1
    )

    is_compliant = uptime.uptime_pct >= target_uptime_pct

    if not is_compliant:
        breach_count = int((100 - uptime.uptime_pct) / 0.1)
    else:
        breach_count = 0

    credits_due = 0.0
    if not is_compliant:
        shortfall = target_uptime_pct - uptime.uptime_pct
        credits_due = round(min(shortfall * 100, 100.0), 1)

    return SLAReport(
        period=period.value,
        target_uptime_pct=target_uptime_pct,
        actual_uptime_pct=uptime.uptime_pct,
        is_compliant=is_compliant,
        downtime_minutes=uptime.downtime_minutes,
        allowed_downtime_minutes=allowed_downtime,
        breach_count=breach_count,
        credits_due=credits_due,
    )


async def get_monitor_analytics(
    monitor_id: str,
    period: TimePeriod = TimePeriod.DAYS_30,
) -> MonitorAnalytics:
    """Get comprehensive analytics for a monitor."""
    until = datetime.utcnow()
    since = until - period_to_timedelta(period)

    uptime = await calculate_uptime(monitor_id, since, until)
    response_times = await calculate_response_times(monitor_id, since, until)
    incidents = await calculate_incident_summary(monitor_id, since, until)

    async with async_session_factory() as session:
        monitor = await session.get(Monitor, monitor_id)
        name = monitor.name if monitor else "Unknown"

    return MonitorAnalytics(
        monitor_id=monitor_id,
        monitor_name=name,
        period=period.value,
        uptime=uptime,
        response_time=response_times,
        incidents=incidents,
        total_checks=uptime.total_checks,
    )


async def get_multi_monitor_summary(
    monitor_ids: List[str],
    period: TimePeriod = TimePeriod.DAYS_30,
) -> Dict[str, Any]:
    """Get aggregated analytics across multiple monitors."""
    results = []
    for mid in monitor_ids:
        try:
            analytics = await get_monitor_analytics(mid, period)
            results.append(analytics)
        except Exception as e:
            logger.warning(f"Failed to get analytics for monitor {mid}: {e}")

    if not results:
        return {"error": "No analytics available for given monitors"}

    total_checks = sum(r.total_checks for r in results)
    total_up = sum(r.uptime.up_checks for r in results)
    overall_uptime = round((total_up / total_checks) * 100, 4) if total_checks > 0 else 100.0
    incidents_total = sum(r.incidents.total_incidents for r in results)

    return {
        "period": period.value,
        "monitors_count": len(results),
        "total_checks": total_checks,
        "overall_uptime_pct": overall_uptime,
        "total_incidents": incidents_total,
        "monitors": [
            {
                "id": r.monitor_id,
                "name": r.monitor_name,
                "uptime_pct": r.uptime.uptime_pct,
                "avg_response_ms": r.response_time.avg,
                "p95_response_ms": r.response_time.p95,
                "incidents": r.incidents.total_incidents,
            }
            for r in results
        ],
    }


# ── Trend Analysis ────────────────────────────────────────────────────────────

def detect_trend(data_points: List[float]) -> Tuple[str, float]:
    """
    Detect trend direction using simple linear regression.
    Returns (direction, percentage_change).
    """
    if len(data_points) < 2:
        return ("stable", 0.0)

    n = len(data_points)
    x_mean = (n - 1) / 2.0
    y_mean = sum(data_points) / n

    numerator = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(data_points))
    denominator = sum((i - x_mean) ** 2 for i in range(n))

    if denominator == 0:
        return ("stable", 0.0)

    slope = numerator / denominator

    # Percentage change from first to last
    if data_points[0] != 0:
        pct_change = round(((data_points[-1] - data_points[0]) / data_points[0]) * 100, 2)
    else:
        pct_change = 0.0

    # For response times: increasing = degrading; for uptime: increasing = improving
    if abs(pct_change) < 1.0:
        direction = "stable"
    elif slope > 0:
        direction = "increasing"
    else:
        direction = "decreasing"

    return (direction, pct_change)


def detect_anomalies(
    data_points: List[TrendDataPoint],
    threshold_std: float = 2.0,
) -> List[TrendDataPoint]:
    """Detect anomalies using standard deviation method."""
    if len(data_points) < 3:
        return []

    values = [dp.value for dp in data_points]
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = variance ** 0.5

    if std == 0:
        return []

    anomalies = []
    for dp in data_points:
        if abs(dp.value - mean) > threshold_std * std:
            anomalies.append(dp)

    return anomalies


async def analyze_response_time_trend(
    monitor_id: str,
    period: TimePeriod = TimePeriod.DAYS_7,
    bucket_hours: int = 1,
) -> TrendAnalysis:
    """Analyze response time trend over a period, bucketed by hour."""
    from sqlalchemy import select, func

    until = datetime.utcnow()
    since = until - period_to_timedelta(period)

    async with async_session_factory() as session:
        result = await session.execute(
            select(
                func.strftime("%Y-%m-%d %H:00:00", MonitorCheck.checked_at),
                func.avg(MonitorCheck.response_time_ms),
            )
            .where(
                MonitorCheck.monitor_id == monitor_id,
                MonitorCheck.checked_at >= since,
                MonitorCheck.checked_at <= until,
                MonitorCheck.is_up == True,
                MonitorCheck.response_time_ms > 0,
            )
            .group_by(func.strftime("%Y-%m-%d %H:00:00", MonitorCheck.checked_at))
            .order_by(func.strftime("%Y-%m-%d %H:00:00", MonitorCheck.checked_at))
        )
        rows = result.all()

    data_points = []
    values = []
    for row in rows:
        try:
            ts = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue
        val = float(row[1]) if row[1] else 0.0
        data_points.append(TrendDataPoint(timestamp=ts, value=round(val, 2)))
        values.append(val)

    direction, pct_change = detect_trend(values)
    anomalies = detect_anomalies(data_points)

    # Predict next value using simple linear extrapolation
    predicted = None
    if len(values) >= 2:
        slope = (values[-1] - values[0]) / max(len(values) - 1, 1)
        predicted = round(values[-1] + slope, 2)

    return TrendAnalysis(
        metric_name="response_time_ms",
        period=period.value,
        data_points=data_points,
        trend_direction=direction,
        trend_pct=pct_change,
        predicted_next_value=predicted,
        anomaly_points=anomalies,
    )


async def analyze_uptime_trend(
    monitor_id: str,
    period: TimePeriod = TimePeriod.DAYS_30,
    bucket_days: int = 1,
) -> TrendAnalysis:
    """Analyze uptime percentage trend over a period, bucketed by day."""
    from sqlalchemy import select, func, case

    until = datetime.utcnow()
    since = until - period_to_timedelta(period)

    async with async_session_factory() as session:
        result = await session.execute(
            select(
                func.strftime("%Y-%m-%d", MonitorCheck.checked_at),
                func.count(),
                func.sum(MonitorCheck.is_up.cast(Integer)),
            )
            .where(
                MonitorCheck.monitor_id == monitor_id,
                MonitorCheck.checked_at >= since,
                MonitorCheck.checked_at <= until,
            )
            .group_by(func.strftime("%Y-%m-%d", MonitorCheck.checked_at))
            .order_by(func.strftime("%Y-%m-%d", MonitorCheck.checked_at))
        )
        rows = result.all()

    data_points = []
    values = []
    for row in rows:
        try:
            ts = datetime.strptime(row[0], "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        total = int(row[1]) if row[1] else 0
        up = int(row[2]) if row[2] else 0
        pct = round((up / total) * 100, 2) if total > 0 else 100.0
        data_points.append(TrendDataPoint(timestamp=ts, value=pct, label=f"{pct}%"))
        values.append(pct)

    direction, pct_change = detect_trend(values)
    anomalies = detect_anomalies(data_points)

    predicted = None
    if len(values) >= 2:
        slope = (values[-1] - values[0]) / max(len(values) - 1, 1)
        predicted = round(min(100.0, max(0.0, values[-1] + slope)), 2)

    # For uptime, invert direction semantics
    if direction == "increasing":
        direction_label = "improving"
    elif direction == "decreasing":
        direction_label = "degrading"
    else:
        direction_label = "stable"

    return TrendAnalysis(
        metric_name="uptime_pct",
        period=period.value,
        data_points=data_points,
        trend_direction=direction_label,
        trend_pct=pct_change,
        predicted_next_value=predicted,
        anomaly_points=anomalies,
    )


# ── Export Functions ──────────────────────────────────────────────────────────

def export_uptime_csv(uptime_records: List[UptimeRecord]) -> str:
    """Export uptime records as CSV string."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Period", "Total Checks", "Up", "Down",
        "Uptime %", "Downtime Minutes",
    ])
    for rec in uptime_records:
        writer.writerow([
            rec.period, rec.total_checks, rec.up_checks, rec.down_checks,
            rec.uptime_pct, rec.downtime_minutes,
        ])
    return output.getvalue()


def export_response_times_csv(monitor_id: str) -> str:
    """Placeholder for CSV export of response times."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Monitor ID", "Timestamp", "Response Time (ms)", "Status"])
    writer.writerow([monitor_id, datetime.utcnow().isoformat(), 0, "placeholder"])
    return output.getvalue()


def export_analytics_json(analytics: MonitorAnalytics) -> str:
    """Export analytics as JSON string."""
    data = {
        "monitor_id": analytics.monitor_id,
        "monitor_name": analytics.monitor_name,
        "period": analytics.period,
        "uptime": {
            "total_checks": analytics.uptime.total_checks,
            "uptime_pct": analytics.uptime.uptime_pct,
            "downtime_minutes": analytics.uptime.downtime_minutes,
        },
        "response_time": {
            "p50": analytics.response_time.p50,
            "p95": analytics.response_time.p95,
            "avg": analytics.response_time.avg,
        },
        "incidents": {
            "total": analytics.incidents.total_incidents,
            "resolved": analytics.incidents.resolved_incidents,
            "mttr_minutes": analytics.incidents.mttr_minutes,
        },
    }
    return json.dumps(data, indent=2)


def format_uptime_for_display(uptime_pct: float) -> str:
    """Format uptime percentage for human display."""
    if uptime_pct >= 99.99:
        return "99.99%+ (Excellent)"
    elif uptime_pct >= 99.9:
        return f"{uptime_pct:.3f}% (Good)"
    elif uptime_pct >= 99.0:
        return f"{uptime_pct:.2f}% (Fair)"
    elif uptime_pct >= 95.0:
        return f"{uptime_pct:.1f}% (Poor)"
    else:
        return f"{uptime_pct:.1f}% (Critical)"


def format_sla_compliance(report: SLAReport) -> str:
    """Format SLA report for display."""
    status = "✅ Compliant" if report.is_compliant else "❌ Breach"
    lines = [
        f"SLA Report ({report.period})",
        f"Status: {status}",
        f"Target Uptime: {report.target_uptime_pct}%",
        f"Actual Uptime: {report.actual_uptime_pct}%",
        f"Downtime: {report.downtime_minutes:.1f} min (allowed: {report.allowed_downtime_minutes:.1f} min)",
    ]
    if not report.is_compliant:
        lines.append(f"Breach Count: {report.breach_count}")
        lines.append(f"SLA Credits Due: {report.credits_due}%")
    return "\n".join(lines)


def format_trend_analysis(trend: TrendAnalysis) -> str:
    """Format trend analysis for display."""
    arrow = {"improving": "📈", "degrading": "📉", "stable": "➡️", "increasing": "📈", "decreasing": "📉"}
    icon = arrow.get(trend.trend_direction, "•")
    lines = [
        f"{icon} {trend.metric_name} Trend ({trend.period})",
        f"  Direction: {trend.trend_direction} ({trend.trend_pct:+.1f}%)",
    ]
    if trend.predicted_next_value is not None:
        lines.append(f"  Predicted next: {trend.predicted_next_value}")
    if trend.anomaly_points:
        lines.append(f"  Anomalies detected: {len(trend.anomaly_points)}")
    return "\n".join(lines)


# ── Comparison Functions ──────────────────────────────────────────────────────

def compare_periods(
    current: UptimeRecord,
    previous: UptimeRecord,
) -> Dict[str, Any]:
    """Compare two uptime records and return delta analysis."""
    uptime_delta = round(current.uptime_pct - previous.uptime_pct, 4)
    downtime_delta = round(current.downtime_minutes - previous.downtime_minutes, 1)
    checks_delta = current.total_checks - previous.total_checks

    return {
        "uptime_pct": {
            "current": current.uptime_pct,
            "previous": previous.uptime_pct,
            "delta": uptime_delta,
            "improved": uptime_delta > 0,
        },
        "downtime_minutes": {
            "current": current.downtime_minutes,
            "previous": previous.downtime_minutes,
            "delta": downtime_delta,
            "improved": downtime_delta < 0,
        },
        "total_checks": {
            "current": current.total_checks,
            "previous": previous.total_checks,
            "delta": checks_delta,
        },
    }


def compare_response_time_periods(
    current: PercentileStats,
    previous: PercentileStats,
) -> Dict[str, Any]:
    """Compare response time percentiles between two periods."""
    delta_p50 = round(current.p50 - previous.p50, 2)
    delta_p95 = round(current.p95 - previous.p95, 2)
    delta_avg = round(current.avg - previous.avg, 2)

    return {
        "p50": {"current": current.p50, "previous": previous.p50, "delta": delta_p50, "improved": delta_p50 < 0},
        "p95": {"current": current.p95, "previous": previous.p95, "delta": delta_p95, "improved": delta_p95 < 0},
        "avg": {"current": current.avg, "previous": previous.avg, "delta": delta_avg, "improved": delta_avg < 0},
    }


# ── Dashboard Summary ─────────────────────────────────────────────────────────

async def get_dashboard_summary(
    user_id: str,
    period: TimePeriod = TimePeriod.DAYS_30,
) -> Dict[str, Any]:
    """Get a complete dashboard summary for a user."""
    from sqlalchemy import select

    async with async_session_factory() as session:
        result = await session.execute(
            select(Monitor).where(Monitor.owner_id == user_id)
        )
        monitors = result.scalars().all()

    monitor_ids = [m.id for m in monitors]
    summary = await get_multi_monitor_summary(monitor_ids, period)

    monitor_statuses = []
    for m in monitors:
        monitor_statuses.append({
            "id": m.id,
            "name": m.name,
            "url": m.url,
            "status": m.status,
            "type": m.type,
            "uptime_pct": m.uptime_percentage,
            "avg_response_ms": m.avg_response_time_ms,
            "last_check_at": m.last_check_at.isoformat() if m.last_check_at else None,
        })

    return {
        "period": period.value,
        "summary": summary,
        "monitors": monitor_statuses,
        "total_monitors": len(monitors),
    }
