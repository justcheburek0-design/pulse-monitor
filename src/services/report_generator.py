"""Pulse report generator service.

Generates comprehensive monitoring reports in multiple formats:
- Text/Markdown reports for email and Telegram
- JSON reports for API consumption
- CSV exports for spreadsheet analysis
- HTML reports for web dashboard embedding

Supports scheduled and on-demand report generation with
configurable templates and delivery channels.
"""

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from src.config.database import async_session_factory
from src.models.monitor import Monitor, MonitorCheck
from src.services.analytics_service import (
    TimePeriod,
    calculate_uptime,
    calculate_response_times,
    calculate_incident_summary,
    generate_sla_report,
    get_monitor_analytics,
    get_multi_monitor_summary,
    format_uptime_for_display,
    format_sla_compliance,
)

logger = logging.getLogger(__name__)


# ── Report Types ───────────────────────────────────────────────────────────────

class ReportFormat(Enum):
    MARKDOWN = "markdown"
    JSON = "json"
    CSV = "csv"
    HTML = "html"
    TEXT = "text"


class ReportType(Enum):
    SINGLE_MONITOR = "single_monitor"
    ALL_MONITORS = "all_monitors"
    SLA = "sla"
    INCIDENT = "incident"
    WEEKLY_SUMMARY = "weekly_summary"
    MONTHLY_SUMMARY = "monthly_summary"


@dataclass
class ReportConfig:
    """Configuration for report generation."""
    report_type: ReportType
    format: ReportFormat
    period: TimePeriod = TimePeriod.DAYS_30
    monitor_ids: Optional[List[str]] = None
    user_id: Optional[str] = None
    include_response_times: bool = True
    include_incidents: bool = True
    include_sla: bool = True
    include_trends: bool = True
    title: Optional[str] = None
    target_uptime_pct: float = 99.9


@dataclass
class GeneratedReport:
    """A generated report with metadata."""
    config: ReportConfig
    content: str
    generated_at: datetime
    format: ReportFormat
    size_bytes: int
    monitor_count: int = 0
    error: Optional[str] = None

    @property
    def is_success(self) -> bool:
        return self.error is None


# ── Markdown Report Generator ─────────────────────────────────────────────────

def _md_header(title: str, level: int = 1) -> str:
    return f"{'#' * level} {title}\n\n"


def _md_table(headers: List[str], rows: List[List[str]]) -> str:
    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines) + "\n\n"


def _md_status_badge(status: str) -> str:
    badges = {
        "up": "🟢 UP",
        "down": "🔴 DOWN",
        "paused": "⏸️ PAUSED",
        "error": "⚠️ ERROR",
        "pending": "⏳ PENDING",
    }
    return badges.get(status.lower(), f"❓ {status}")


async def generate_single_monitor_markdown(
    monitor_id: str,
    period: TimePeriod = TimePeriod.DAYS_30,
    include_response_times: bool = True,
    include_incidents: bool = True,
    include_sla: bool = True,
) -> str:
    """Generate a Markdown report for a single monitor."""
    analytics = await get_monitor_analytics(monitor_id, period)
    lines = []

    lines.append(_md_header(f"Monitor Report: {analytics.monitor_name}"))
    lines.append(f"**Period:** {analytics.period}\n")
    lines.append(f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n")
    lines.append("---\n")

    # Uptime section
    lines.append(_md_header("Uptime", 2))
    lines.append(f"**Overall Uptime:** {format_uptime_for_display(analytics.uptime.uptime_pct)}\n")
    lines.append(f"- Total Checks: {analytics.uptime.total_checks}")
    lines.append(f"- Up: {analytics.uptime.up_checks}")
    lines.append(f"- Down: {analytics.uptime.down_checks}")
    lines.append(f"- Downtime: {analytics.uptime.downtime_minutes:.1f} minutes\n")

    # Response times
    if include_response_times and analytics.response_time.count > 0:
        lines.append(_md_header("Response Times", 2))
        rt = analytics.response_time
        lines.append(_md_table(
            ["Metric", "Value (ms)"],
            [
                ["Average", f"{rt.avg:.1f}"],
                ["Median (p50)", f"{rt.p50:.1f}"],
                ["p75", f"{rt.p75:.1f}"],
                ["p90", f"{rt.p90:.1f}"],
                ["p95", f"{rt.p95:.1f}"],
                ["p99", f"{rt.p99:.1f}"],
                ["Min", f"{rt.min:.1f}"],
                ["Max", f"{rt.max:.1f}"],
            ],
        ))

    # Incidents
    if include_incidents:
        lines.append(_md_header("Incidents", 2))
        inc = analytics.incidents
        lines.append(f"- Total: {inc.total_incidents}")
        lines.append(f"- Resolved: {inc.resolved_incidents}")
        lines.append(f"- Active: {inc.active_incidents}")
        if inc.avg_resolution_minutes > 0:
            lines.append(f"- Avg Resolution Time: {inc.avg_resolution_minutes:.1f} min")
            lines.append(f"- MTTR: {inc.mttr_minutes:.1f} min")
        lines.append("")

        if inc.by_severity:
            lines.append("**By Severity:**\n")
            for sev, count in inc.by_severity.items():
                lines.append(f"- {sev}: {count}")
            lines.append("")

    # SLA
    if include_sla:
        sla = await generate_sla_report(monitor_id, period)
        lines.append(_md_header("SLA Compliance", 2))
        lines.append(format_sla_compliance(sla))
        lines.append("")

    lines.append("---\n")
    lines.append(f"_Report generated by Pulse Monitor at {datetime.utcnow().isoformat()}_\n")

    return "".join(lines)


async def generate_all_monitors_markdown(
    monitor_ids: List[str],
    period: TimePeriod = TimePeriod.DAYS_30,
) -> str:
    """Generate a Markdown report for all monitors."""
    summary = await get_multi_monitor_summary(monitor_ids, period)
    lines = []

    title = "All Monitors Report"
    lines.append(_md_header(title))
    lines.append(f"**Period:** {period.value}\n")
    lines.append(f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n")
    lines.append("---\n")

    lines.append(_md_header("Summary", 2))
    lines.append(f"- Total Monitors: {summary.get('monitors_count', 0)}")
    lines.append(f"- Total Checks: {summary.get('total_checks', 0)}")
    lines.append(f"- Overall Uptime: {summary.get('overall_uptime_pct', 0):.2f}%")
    lines.append(f"- Total Incidents: {summary.get('total_incidents', 0)}\n")

    monitors = summary.get("monitors", [])
    if monitors:
        lines.append(_md_header("Monitor Status", 2))
        rows = []
        for m in monitors:
            uptime_str = format_uptime_for_display(m.get("uptime_pct", 0))
            rows.append([
                m.get("name", "Unknown"),
                f"{m.get('uptime_pct', 0):.2f}%",
                f"{m.get('avg_response_ms', 0):.0f}",
                f"{m.get('p95_response_ms', 0):.0f}",
                str(m.get("incidents", 0)),
            ])
        lines.append(_md_table(
            ["Monitor", "Uptime", "Avg RT (ms)", "p95 RT (ms)", "Incidents"],
            rows,
        ))

    lines.append("---\n")
    lines.append(f"_Report generated by Pulse Monitor at {datetime.utcnow().isoformat()}_\n")

    return "".join(lines)


# ── JSON Report Generator ─────────────────────────────────────────────────────

async def generate_json_report(
    config: ReportConfig,
) -> str:
    """Generate a JSON report based on config."""
    data: Dict[str, Any] = {
        "report_type": config.report_type.value,
        "format": "json",
        "period": config.period.value,
        "generated_at": datetime.utcnow().isoformat(),
    }

    if config.report_type == ReportType.SINGLE_MONITOR and config.monitor_ids:
        analytics = await get_monitor_analytics(config.monitor_ids[0], config.period)
        data["monitor"] = {
            "id": analytics.monitor_id,
            "name": analytics.monitor_name,
            "uptime_pct": analytics.uptime.uptime_pct,
            "total_checks": analytics.uptime.total_checks,
            "downtime_minutes": analytics.uptime.downtime_minutes,
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

    elif config.report_type == ReportType.ALL_MONITORS and config.monitor_ids:
        summary = await get_multi_monitor_summary(config.monitor_ids, config.period)
        data["summary"] = summary

    elif config.report_type == ReportType.SLA and config.monitor_ids:
        sla = await generate_sla_report(
            config.monitor_ids[0], config.period, config.target_uptime_pct
        )
        data["sla"] = {
            "period": sla.period,
            "target_uptime_pct": sla.target_uptime_pct,
            "actual_uptime_pct": sla.actual_uptime_pct,
            "is_compliant": sla.is_compliant,
            "downtime_minutes": sla.downtime_minutes,
            "allowed_downtime_minutes": sla.allowed_downtime_minutes,
            "breach_count": sla.breach_count,
            "credits_due": sla.credits_due,
        }

    return json.dumps(data, indent=2, default=str)


# ── CSV Report Generator ──────────────────────────────────────────────────────

async def generate_csv_report(
    monitor_ids: List[str],
    period: TimePeriod = TimePeriod.DAYS_30,
) -> str:
    """Generate a CSV report for multiple monitors."""
    output = io.StringIO()
    writer = io.StringIO()

    # Summary section
    summary = await get_multi_monitor_summary(monitor_ids, period)

    lines = []
    lines.append("# Pulse Monitor Report")
    lines.append(f"# Period: {period.value}")
    lines.append(f"# Generated: {datetime.utcnow().isoformat()}")
    lines.append(f"# Total Monitors: {summary.get('monitors_count', 0)}")
    lines.append(f"# Overall Uptime: {summary.get('overall_uptime_pct', 0):.2f}%")
    lines.append("")
    lines.append("monitor_name,uptime_pct,avg_response_ms,p95_response_ms,incidents")

    for m in summary.get("monitors", []):
        lines.append(
            f"{m.get('name', 'Unknown')},{m.get('uptime_pct', 0):.2f},"
            f"{m.get('avg_response_ms', 0):.1f},{m.get('p95_response_ms', 0):.1f},"
            f"{m.get('incidents', 0)}"
        )

    return "\n".join(lines)


# ── HTML Report Generator ─────────────────────────────────────────────────────

async def generate_html_report(
    monitor_ids: List[str],
    period: TimePeriod = TimePeriod.DAYS_30,
    title: str = "Pulse Monitor Report",
) -> str:
    """Generate an HTML report for web dashboard embedding."""
    summary = await get_multi_monitor_summary(monitor_ids, period)

    monitor_rows = ""
    for m in summary.get("monitors", []):
        uptime = m.get("uptime_pct", 0)
        if uptime >= 99.9:
            color = "#22c55e"
        elif uptime >= 99.0:
            color = "#eab308"
        else:
            color = "#ef4444"

        monitor_rows += f"""
        <tr>
            <td>{m.get('name', 'Unknown')}</td>
            <td style="color: {color}; font-weight: bold;">{uptime:.2f}%</td>
            <td>{m.get('avg_response_ms', 0):.0f}</td>
            <td>{m.get('p95_response_ms', 0):.0f}</td>
            <td>{m.get('incidents', 0)}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #0f172a; color: #e2e8f0; }}
        .container {{ max-width: 900px; margin: 0 auto; }}
        h1 {{ color: #f8fafc; border-bottom: 2px solid #334155; padding-bottom: 10px; }}
        .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin: 20px 0; }}
        .card {{ background: #1e293b; border-radius: 8px; padding: 16px; border: 1px solid #334155; }}
        .card h3 {{ margin: 0 0 8px 0; color: #94a3b8; font-size: 0.85em; text-transform: uppercase; }}
        .card .value {{ font-size: 1.8em; font-weight: bold; color: #f8fafc; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        th {{ text-align: left; padding: 12px; background: #1e293b; color: #94a3b8; font-size: 0.85em; text-transform: uppercase; border-bottom: 2px solid #334155; }}
        td {{ padding: 12px; border-bottom: 1px solid #1e293b; }}
        tr:hover td {{ background: #1e293b; }}
        .footer {{ margin-top: 30px; color: #475569; font-size: 0.85em; text-align: center; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🔍 {title}</h1>
        <p style="color: #64748b;">Period: {period.value} | Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>

        <div class="summary">
            <div class="card">
                <h3>Monitors</h3>
                <div class="value">{summary.get('monitors_count', 0)}</div>
            </div>
            <div class="card">
                <h3>Total Checks</h3>
                <div class="value">{summary.get('total_checks', 0)}</div>
            </div>
            <div class="card">
                <h3>Overall Uptime</h3>
                <div class="value">{summary.get('overall_uptime_pct', 0):.2f}%</div>
            </div>
            <div class="card">
                <h3>Incidents</h3>
                <div class="value">{summary.get('total_incidents', 0)}</div>
            </div>
        </div>

        <table>
            <thead>
                <tr>
                    <th>Monitor</th>
                    <th>Uptime</th>
                    <th>Avg RT (ms)</th>
                    <th>p95 RT (ms)</th>
                    <th>Incidents</th>
                </tr>
            </thead>
            <tbody>
                {monitor_rows}
            </tbody>
        </table>

        <div class="footer">
            Pulse Monitor &mdash; {datetime.utcnow().year}
        </div>
    </div>
</body>
</html>"""

    return html


# ── Text Report Generator (for Telegram/email) ────────────────────────────────

async def generate_text_report(
    monitor_ids: List[str],
    period: TimePeriod = TimePeriod.DAYS_30,
) -> str:
    """Generate a plain text report suitable for Telegram or email."""
    summary = await get_multi_monitor_summary(monitor_ids, period)
    lines = []

    lines.append("🔍 PULSE MONITOR REPORT")
    lines.append(f"Period: {period.value}")
    lines.append(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("─" * 40)
    lines.append("")
    lines.append(f"📊 Monitors: {summary.get('monitors_count', 0)}")
    lines.append(f"🔢 Total Checks: {summary.get('total_checks', 0)}")
    lines.append(f"✅ Overall Uptime: {summary.get('overall_uptime_pct', 0):.2f}%")
    lines.append(f"🚨 Incidents: {summary.get('total_incidents', 0)}")
    lines.append("")
    lines.append("─" * 40)
    lines.append("")

    for m in summary.get("monitors", []):
        uptime = m.get("uptime_pct", 0)
        if uptime >= 99.9:
            icon = "🟢"
        elif uptime >= 99.0:
            icon = "🟡"
        else:
            icon = "🔴"

        lines.append(f"{icon} {m.get('name', 'Unknown')}")
        lines.append(f"   Uptime: {uptime:.2f}%")
        lines.append(f"   Avg RT: {m.get('avg_response_ms', 0):.0f}ms | p95: {m.get('p95_response_ms', 0):.0f}ms")
        lines.append(f"   Incidents: {m.get('incidents', 0)}")
        lines.append("")

    lines.append("─" * 40)
    lines.append("Pulse Monitor")

    return "\n".join(lines)


# ── Weekly/Monthly Summary ────────────────────────────────────────────────────

async def generate_weekly_summary(
    monitor_ids: List[str],
) -> str:
    """Generate a weekly summary report in Markdown."""
    return await generate_all_monitors_markdown(monitor_ids, TimePeriod.DAYS_7)


async def generate_monthly_summary(
    monitor_ids: List[str],
) -> str:
    """Generate a monthly summary report in Markdown."""
    return await generate_all_monitors_markdown(monitor_ids, TimePeriod.DAYS_30)


# ── Main Report Generation Entry Point ────────────────────────────────────────

async def generate_report(config: ReportConfig) -> GeneratedReport:
    """Generate a report based on the provided configuration."""
    generated_at = datetime.utcnow()
    content = ""
    error = None
    monitor_count = 0

    try:
        if config.report_type == ReportType.SINGLE_MONITOR and config.monitor_ids:
            monitor_count = 1
            if config.format == ReportFormat.MARKDOWN:
                content = await generate_single_monitor_markdown(
                    config.monitor_ids[0],
                    config.period,
                    config.include_response_times,
                    config.include_incidents,
                    config.include_sla,
                )
            elif config.format == ReportFormat.JSON:
                content = await generate_json_report(config)
            elif config.format == ReportFormat.TEXT:
                content = await generate_text_report(config.monitor_ids[:1], config.period)
            elif config.format == ReportFormat.HTML:
                content = await generate_html_report(
                    config.monitor_ids[:1], config.period,
                    title=config.title or "Monitor Report",
                )
            else:
                content = await generate_json_report(config)

        elif config.report_type in (ReportType.ALL_MONITORS, ReportType.WEEKLY_SUMMARY, ReportType.MONTHLY_SUMMARY):
            ids = config.monitor_ids or []
            monitor_count = len(ids)

            if config.format == ReportFormat.MARKDOWN:
                content = await generate_all_monitors_markdown(ids, config.period)
            elif config.format == ReportFormat.JSON:
                content = await generate_json_report(config)
            elif config.format == ReportFormat.CSV:
                content = await generate_csv_report(ids, config.period)
            elif config.format == ReportFormat.HTML:
                content = await generate_html_report(
                    ids, config.period,
                    title=config.title or "All Monitors Report",
                )
            elif config.format == ReportFormat.TEXT:
                content = await generate_text_report(ids, config.period)
            else:
                content = await generate_json_report(config)

        elif config.report_type == ReportType.SLA and config.monitor_ids:
            monitor_count = 1
            if config.format == ReportFormat.MARKDOWN:
                analytics = await get_monitor_analytics(config.monitor_ids[0], config.period)
                sla = await generate_sla_report(
                    config.monitor_ids[0], config.period, config.target_uptime_pct
                )
                lines = [
                    _md_header(f"SLA Report: {analytics.monitor_name}"),
                    format_sla_compliance(sla),
                    "",
                ]
                content = "\n".join(lines)
            else:
                content = await generate_json_report(config)

        else:
            error = f"Unsupported report type: {config.report_type.value}"

    except Exception as e:
        error = str(e)
        logger.error(f"Report generation failed: {e}")

    return GeneratedReport(
        config=config,
        content=content,
        generated_at=generated_at,
        format=config.format,
        size_bytes=len(content.encode("utf-8")),
        monitor_count=monitor_count,
        error=error,
    )


# ── Report History Tracker ────────────────────────────────────────────────────

@dataclass
class ReportHistoryEntry:
    report_type: str
    format: str
    period: str
    generated_at: datetime
    size_bytes: int
    monitor_count: int
    success: bool


_report_history: List[ReportHistoryEntry] = []


def track_report(report: GeneratedReport) -> None:
    """Track a generated report in history."""
    _report_history.append(ReportHistoryEntry(
        report_type=report.config.report_type.value,
        format=report.format.value,
        period=report.config.period.value,
        generated_at=report.generated_at,
        size_bytes=report.size_bytes,
        monitor_count=report.monitor_count,
        success=report.is_success,
    ))


def get_report_history(limit: int = 50) -> List[ReportHistoryEntry]:
    """Get recent report generation history."""
    return _report_history[-limit:]


def get_report_stats() -> Dict[str, Any]:
    """Get report generation statistics."""
    if not _report_history:
        return {"total": 0}

    total = len(_report_history)
    successful = sum(1 for r in _report_history if r.success)
    by_type: Dict[str, int] = {}
    by_format: Dict[str, int] = {}

    for entry in _report_history:
        by_type[entry.report_type] = by_type.get(entry.report_type, 0) + 1
        by_format[entry.format] = by_format.get(entry.format, 0) + 1

    return {
        "total": total,
        "successful": successful,
        "failed": total - successful,
        "success_rate": round((successful / total) * 100, 1),
        "by_type": by_type,
        "by_format": by_format,
        "total_size_bytes": sum(r.size_bytes for r in _report_history),
    }
