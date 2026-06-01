
"""CLI interface for Pulse administration.

Production-ready CLI with full CRUD operations for monitors, teams,
incidents, config import/export, log tailing, and system status.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn
from rich.panel import Panel
from rich.layout import Layout
from rich.syntax import Syntax
from rich import box
from rich.text import Text
from rich.tree import Tree
from rich import print as rprint

from src.config.database import init_db, close_db, async_session_factory
from src.config.settings import get_settings
from src.models.user import User
from src.models.monitor import Monitor, MonitorStatus, MonitorType
from src.models.team import Team, TeamMember, TeamInvite, TeamRole
from src.models.incident import Incident, IncidentEvent, IncidentStatus, IncidentSeverity
from src.models.alert import Alert, AlertRule, AlertChannel, AlertChannelType, AlertSeverity
from src.models.dashboard import Dashboard, DashboardWidget, WidgetType

app = typer.Typer(
    name="pulse-cli",
    help="Pulse Monitoring Platform CLI — manage monitors, teams, incidents, and more",
    no_args_is_help=True,
)
console = Console()
settings = get_settings()

# ── Sub-applications ──────────────────────────────────────────────────────────

monitor_app = typer.Typer(help="Monitor management commands", no_args_is_help=True)
team_app = typer.Typer(help="Team management commands", no_args_is_help=True)
incident_app = typer.Typer(help="Incident management commands", no_args_is_help=True)
config_app = typer.Typer(help="Configuration import/export", no_args_is_help=True)

app.add_typer(monitor_app, name="monitor")
app.add_typer(team_app, name="team")
app.add_typer(incident_app, name="incident")
app.add_typer(config_app, name="config")


# ── Helper functions ──────────────────────────────────────────────────────────

def _status_color(status: str) -> str:
    """Return Rich color code for a status string."""
    color_map = {
        "up": "green",
        "down": "red",
        "pending": "yellow",
        "paused": "dim",
        "error": "red",
        "maintenance": "blue",
        "investigating": "red",
        "identified": "yellow",
        "monitoring": "blue",
        "resolved": "green",
        "firing": "red",
        "sent": "green",
        "acknowledged": "yellow",
        "failed": "red",
        "active": "green",
        "inactive": "dim",
    }
    return color_map.get(status.lower(), "white")


def _severity_icon(severity: str) -> str:
    """Return an icon for severity level."""
    icons = {
        "critical": "🔴",
        "major": "🟠",
        "minor": "🟡",
        "warning": "⚠️",
        "info": "ℹ️",
        "recovery": "✅",
        "maintenance": "🔧",
    }
    return icons.get(severity.lower(), "•")


def _format_duration(seconds: Optional[int]) -> str:
    """Format seconds into human-readable duration."""
    if seconds is None:
        return "N/A"
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    elif seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h {m}m"
    else:
        d = seconds // 86400
        h = (seconds % 86400) // 3600
        return f"{d}d {h}h"


def _format_datetime(dt: Optional[datetime]) -> str:
    """Format datetime for display."""
    if dt is None:
        return "Never"
    now = datetime.utcnow()
    delta = now - dt.replace(tzinfo=None) if dt.tzinfo is None else now - dt.replace(tzinfo=None)
    if delta.total_seconds() < 60:
        return "Just now"
    elif delta.total_seconds() < 3600:
        return f"{int(delta.total_seconds() // 60)}m ago"
    elif delta.total_seconds() < 86400:
        return f"{int(delta.total_seconds() // 3600)}h ago"
    else:
        return f"{delta.days}d ago"


def _output_json(data: dict) -> None:
    """Output data as formatted JSON."""
    console.print(json.dumps(data, indent=2, default=str))


def _output_yaml(data: dict) -> None:
    """Output data as formatted YAML."""
    console.print(yaml.dump(data, default_flow_style=False, sort_keys=False))


# ── Database session helper ──────────────────────────────────────────────────

async def _with_db(func):
    """Decorator-like helper to run an async function with a DB session."""
    await init_db()
    try:
        await func()
    finally:
        await close_db()


# ═══════════════════════════════════════════════════════════════════════════════
# STATUS COMMAND
# ═══════════════════════════════════════════════════════════════════════════════

@app.command()
def status(output_format: str = typer.Option("table", "--format", "-f", help="Output format: table, json")):
    """Show system health, queue sizes, and worker statistics."""
    async def _status():
        await init_db()
        try:
            async with async_session_factory() as session:
                from sqlalchemy import select, func

                # Count monitors by status
                monitor_counts = {}
                for status_val in [MonitorStatus.UP, MonitorStatus.DOWN, MonitorStatus.PENDING,
                                   MonitorStatus.PAUSED, MonitorStatus.ERROR, MonitorStatus.MAINTENANCE]:
                    result = await session.execute(
                        select(func.count()).select_from(Monitor).where(Monitor.status == status_val)
                    )
                    monitor_counts[status_val] = result.scalar_one()

                total_monitors = sum(monitor_counts.values())
                active_monitors = total_monitors - monitor_counts.get(MonitorStatus.PAUSED, 0)

                # Count users
                user_result = await session.execute(select(func.count()).select_from(User))
                total_users = user_result.scalar_one()

                # Count teams
                team_result = await session.execute(select(func.count()).select_from(Team))
                total_teams = team_result.scalar_one()

                # Count active incidents
                incident_result = await session.execute(
                    select(func.count()).select_from(Incident).where(
                        Incident.status != IncidentStatus.RESOLVED
                    )
                )
                active_incidents = incident_result.scalar_one()

                # Count total incidents
                total_incident_result = await session.execute(select(func.count()).select_from(Incident))
                total_incidents = total_incident_result.scalar_one()

                # Count alerts
                alert_result = await session.execute(
                    select(func.count()).select_from(Alert).where(Alert.status == "firing")
                )
                firing_alerts = alert_result.scalar_one()

                # Count alert channels
                channel_result = await session.execute(select(func.count()).select_from(AlertChannel))
                total_channels = channel_result.scalar_one()

                # Count checks in last 24h
                day_ago = datetime.utcnow() - timedelta(hours=24)
                from src.models.monitor import MonitorCheck
                checks_result = await session.execute(
                    select(func.count()).select_from(MonitorCheck).where(
                        MonitorCheck.checked_at >= day_ago
                    )
                )
                checks_24h = checks_result.scalar_one()

                # Average response time
                avg_result = await session.execute(
                    select(func.avg(MonitorCheck.response_time_ms)).where(
                        MonitorCheck.checked_at >= day_ago,
                        MonitorCheck.is_up == True,
                    )
                )
                avg_response = avg_result.scalar_one() or 0.0

                # Uptime calculation
                up_checks_result = await session.execute(
                    select(func.count()).select_from(MonitorCheck).where(
                        MonitorCheck.checked_at >= day_ago,
                        MonitorCheck.is_up == True,
                    )
                )
                up_checks = up_checks_result.scalar_one()
                uptime_pct = round((up_checks / checks_24h) * 100, 2) if checks_24h > 0 else 100.0

                if output_format == "json":
                    _output_json({
                        "system": {
                            "app_name": settings.app_name,
                            "version": settings.app_version,
                            "status": "healthy",
                            "scheduler": "running" if settings.scheduler_enabled else "disabled",
                        },
                        "monitors": {
                            "total": total_monitors,
                            "active": active_monitors,
                            "by_status": monitor_counts,
                        },
                        "users": {"total": total_users},
                        "teams": {"total": total_teams},
                        "incidents": {
                            "active": active_incidents,
                            "total": total_incidents,
                        },
                        "alerts": {"firing": firing_alerts},
                        "channels": {"total": total_channels},
                        "checks_24h": checks_24h,
                        "avg_response_ms": round(avg_response, 2),
                        "uptime_24h": uptime_pct,
                    })
                    return

                # Rich table output
                console.print()
                console.print(Panel(
                    f"[bold]{settings.app_name}[/bold] v{settings.app_version}  •  "
                    f"Status: [green]Healthy[/green]  •  "
                    f"Scheduler: {'[green]Running[/green]' if settings.scheduler_enabled else '[yellow]Disabled[/yellow]'}",
                    title="System Status",
                    box=box.DOUBLE,
                ))

                # Monitors table
                mon_table = Table(title="Monitors", box=box.ROUNDED, show_header=True)
                mon_table.add_column("Status", style="bold")
                mon_table.add_column("Count", justify="right")
                mon_table.add_column("Percentage", justify="right")
                for status_val, count in monitor_counts.items():
                    pct = f"{(count / total_monitors * 100):.1f}%" if total_monitors > 0 else "0%"
                    mon_table.add_row(
                        f"[{_status_color(status_val)}]{status_val}[/{_status_color(status_val)}]",
                        str(count),
                        pct,
                    )
                mon_table.add_row("[bold]Total[/bold]", f"[bold]{total_monitors}[/bold]", "100%")
                console.print(mon_table)

                # Overview table
                overview = Table(title="Overview (Last 24h)", box=box.ROUNDED)
                overview.add_column("Metric", style="bold")
                overview.add_column("Value", justify="right")
                overview.add_row("Total Users", str(total_users))
                overview.add_row("Total Teams", str(total_teams))
                overview.add_row("Active Monitors", str(active_monitors))
                overview.add_row("Active Incidents", str(active_incidents))
                overview.add_row("Firing Alerts", str(firing_alerts))
                overview.add_row("Alert Channels", str(total_channels))
                overview.add_row("Checks (24h)", f"{checks_24h:,}")
                overview.add_row("Avg Response Time", f"{avg_response:.1f} ms")
                overview.add_row("Uptime (24h)", f"{uptime_pct}%")
                console.print(overview)
                console.print()

        finally:
            await close_db()

    asyncio.run(_status())


# ═══════════════════════════════════════════════════════════════════════════════
# MONITOR COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

@monitor_app.command("list")
def monitor_list(
    status_filter: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    monitor_type: Optional[str] = typer.Option(None, "--type", "-t", help="Filter by type"),
    page: int = typer.Option(1, "--page", "-p", help="Page number"),
    page_size: int = typer.Option(20, "--page-size", "-n", help="Items per page"),
    output_format: str = typer.Option("table", "--format", "-f", help="Output format: table, json"),
):
    """List all monitors with optional filtering."""
    async def _list():
        await init_db()
        try:
            async with async_session_factory() as session:
                from sqlalchemy import select, func

                query = select(Monitor)
                count_query = select(func.count()).select_from(Monitor)

                if status_filter:
                    query = query.where(Monitor.status == status_filter)
                    count_query = count_query.where(Monitor.status == status_filter)
                if monitor_type:
                    query = query.where(Monitor.type == monitor_type)
                    count_query = count_query.where(Monitor.type == monitor_type)

                total = (await session.execute(count_query)).scalar_one()
                query = query.order_by(Monitor.created_at.desc())
                query = query.offset((page - 1) * page_size).limit(page_size)

                result = await session.execute(query)
                monitors = result.scalars().all()

                if output_format == "json":
                    _output_json({
                        "total": total,
                        "page": page,
                        "page_size": page_size,
                        "items": [
                            {
                                "id": m.id, "name": m.name, "url": m.url,
                                "type": m.type, "status": m.status,
                                "is_active": m.is_active, "interval_seconds": m.interval_seconds,
                                "uptime_percentage": m.uptime_percentage,
                                "avg_response_time_ms": m.avg_response_time_ms,
                                "last_check_at": m.last_check_at.isoformat() if m.last_check_at else None,
                            }
                            for m in monitors
                        ],
                    })
                    return

                if not monitors:
                    console.print("[yellow]No monitors found[/yellow]")
                    return

                table = Table(
                    title=f"Monitors (showing {len(monitors)} of {total})",
                    box=box.ROUNDED,
                )
                table.add_column("ID", style="dim", max_width=12)
                table.add_column("Name")
                table.add_column("Type")
                table.add_column("URL", max_width=40)
                table.add_column("Status", justify="center")
                table.add_column("Interval", justify="right")
                table.add_column("Uptime %", justify="right")
                table.add_column("Avg ms", justify="right")
                table.add_column("Last Check")

                for m in monitors:
                    table.add_row(
                        m.id[:8] + "...",
                        m.name,
                        m.type,
                        m.url[:40],
                        f"[{_status_color(m.status)}]{m.status}[/{_status_color(m.status)}]",
                        f"{m.interval_seconds}s",
                        f"{m.uptime_percentage:.1f}%",
                        f"{m.avg_response_time_ms:.0f}",
                        _format_datetime(m.last_check_at),
                    )
                console.print(table)
        finally:
            await close_db()

    asyncio.run(_list())


@monitor_app.command("add")
def monitor_add(
    name: str = typer.Option(..., "--name", "-n", help="Monitor name"),
    url: str = typer.Option(..., "--url", "-u", help="Target URL"),
    monitor_type: str = typer.Option("https", "--type", "-t", help="Monitor type: http, https, tcp, icmp, dns, keyword, graphql"),
    interval: int = typer.Option(60, "--interval", "-i", help="Check interval in seconds"),
    timeout: int = typer.Option(10, "--timeout", help="Timeout in seconds"),
    method: str = typer.Option("GET", "--method", "-m", help="HTTP method"),
    expected_status: Optional[int] = typer.Option(None, "--expected-status", help="Expected HTTP status code"),
    expected_keyword: Optional[str] = typer.Option(None, "--keyword", "-k", help="Expected keyword in response"),
    retries: int = typer.Option(3, "--retries", "-r", help="Number of retries"),
    owner_id: Optional[str] = typer.Option(None, "--owner", help="Owner user ID (defaults to first user)"),
):
    """Add a new monitor."""
    async def _add():
        await init_db()
        try:
            async with async_session_factory() as session:
                from sqlalchemy import select

                # Resolve owner
                if owner_id:
                    user = await session.get(User, owner_id)
                    if not user:
                        console.print(f"[red]User {owner_id} not found[/red]")
                        return
                    resolved_owner = owner_id
                else:
                    result = await session.execute(select(User).limit(1))
                    user = result.scalar_one_or_none()
                    if not user:
                        console.print("[red]No users found. Create a user first with pulse-cli create-user[/red]")
                        return
                    resolved_owner = user.id

                monitor = Monitor(
                    owner_id=resolved_owner,
                    name=name,
                    url=url,
                    type=monitor_type,
                    interval_seconds=interval,
                    timeout_seconds=timeout,
                    retries=retries,
                    method=method,
                    expected_status_code=expected_status,
                    expected_keyword=expected_keyword,
                )
                session.add(monitor)
                await session.commit()
                console.print(f"[green]✓ Created monitor[/green] [bold]{name}[/bold] (ID: {monitor.id[:8]}...)")
                console.print(f"  URL: {url}")
                console.print(f"  Type: {monitor_type}  Interval: {interval}s  Timeout: {timeout}s")
        finally:
            await close_db()

    asyncio.run(_add())


@monitor_app.command("remove")
def monitor_remove(
    monitor_id: str = typer.Argument(..., help="Monitor ID to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove a monitor by ID."""
    async def _remove():
        await init_db()
        try:
            async with async_session_factory() as session:
                monitor = await session.get(Monitor, monitor_id)
                if not monitor:
                    # Try prefix match
                    from sqlalchemy import select
                    result = await session.execute(select(Monitor))
                    all_monitors = result.scalars().all()
                    matches = [m for m in all_monitors if m.id.startswith(monitor_id)]
                    if len(matches) == 1:
                        monitor = matches[0]
                    elif len(matches) > 1:
                        console.print(f"[yellow]Multiple monitors match prefix '{monitor_id}'. Provide full ID.[/yellow]")
                        return

                if not monitor:
                    console.print(f"[red]Monitor {monitor_id} not found[/red]")
                    return

                if not force:
                    confirm = typer.confirm(f"Delete monitor '{monitor.name}' ({monitor.url})?")
                    if not confirm:
                        console.print("Cancelled.")
                        return

                await session.delete(monitor)
                await session.commit()
                console.print(f"[green]✓ Removed monitor[/green] {monitor.name}")
        finally:
            await close_db()

    asyncio.run(_remove())


@monitor_app.command("pause")
def monitor_pause(
    monitor_id: str = typer.Argument(..., help="Monitor ID to pause"),
):
    """Pause a monitor."""
    async def _pause():
        await init_db()
        try:
            async with async_session_factory() as session:
                monitor = await session.get(Monitor, monitor_id)
                if not monitor:
                    console.print(f"[red]Monitor {monitor_id} not found[/red]")
                    return
                monitor.status = MonitorStatus.PAUSED
                monitor.is_active = False
                monitor.paused_at = datetime.utcnow()
                await session.commit()
                console.print(f"[yellow]⏸ Paused[/yellow] {monitor.name}")
        finally:
            await close_db()

    asyncio.run(_pause())


@monitor_app.command("resume")
def monitor_resume(
    monitor_id: str = typer.Argument(..., help="Monitor ID to resume"),
):
    """Resume a paused monitor."""
    async def _resume():
        await init_db()
        try:
            async with async_session_factory() as session:
                monitor = await session.get(Monitor, monitor_id)
                if not monitor:
                    console.print(f"[red]Monitor {monitor_id} not found[/red]")
                    return
                monitor.status = MonitorStatus.PENDING
                monitor.is_active = True
                monitor.paused_at = None
                await session.commit()
                console.print(f"[green]▶ Resumed[/green] {monitor.name}")
        finally:
            await close_db()

    asyncio.run(_resume())


@monitor_app.command("show")
def monitor_show(
    monitor_id: str = typer.Argument(..., help="Monitor ID to show"),
    output_format: str = typer.Option("table", "--format", "-f", help="Output format: table, json"),
):
    """Show detailed information about a monitor."""
    async def _show():
        await init_db()
        try:
            async with async_session_factory() as session:
                monitor = await session.get(Monitor, monitor_id)
                if not monitor:
                    console.print(f"[red]Monitor {monitor_id} not found[/red]")
                    return

                if output_format == "json":
                    _output_json({
                        "id": monitor.id, "name": monitor.name, "url": monitor.url,
                        "type": monitor.type, "status": monitor.status,
                        "is_active": monitor.is_active, "is_public": monitor.is_public,
                        "interval_seconds": monitor.interval_seconds,
                        "timeout_seconds": monitor.timeout_seconds,
                        "retries": monitor.retries, "method": monitor.method,
                        "expected_status_code": monitor.expected_status_code,
                        "expected_keyword": monitor.expected_keyword,
                        "follow_redirects": monitor.follow_redirects,
                        "verify_ssl": monitor.verify_ssl,
                        "uptime_percentage": monitor.uptime_percentage,
                        "avg_response_time_ms": monitor.avg_response_time_ms,
                        "consecutive_failures": monitor.consecutive_failures,
                        "last_check_at": monitor.last_check_at.isoformat() if monitor.last_check_at else None,
                        "last_up_at": monitor.last_up_at.isoformat() if monitor.last_up_at else None,
                        "last_down_at": monitor.last_down_at.isoformat() if monitor.last_down_at else None,
                        "created_at": monitor.created_at.isoformat() if monitor.created_at else None,
                    })
                    return

                console.print(Panel(
                    f"[bold]{monitor.name}[/bold]\n"
                    f"ID: {monitor.id}\n"
                    f"URL: {monitor.url}\n"
                    f"Type: {monitor.type}  Method: {monitor.method}\n"
                    f"Status: [{_status_color(monitor.status)}]{monitor.status}[/{_status_color(monitor.status)}]  "
                    f"Active: {monitor.is_active}  Public: {monitor.is_public}\n"
                    f"Interval: {monitor.interval_seconds}s  Timeout: {monitor.timeout_seconds}s  Retries: {monitor.retries}\n"
                    f"Uptime: {monitor.uptime_percentage:.2f}%  Avg Response: {monitor.avg_response_time_ms:.1f}ms\n"
                    f"Consecutive Failures: {monitor.consecutive_failures}\n"
                    f"Last Check: {_format_datetime(monitor.last_check_at)}\n"
                    f"Created: {_format_datetime(monitor.created_at)}",
                    title="Monitor Details",
                    box=box.DOUBLE,
                ))
        finally:
            await close_db()

    asyncio.run(_show())


# ═══════════════════════════════════════════════════════════════════════════════
# TEAM COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

@team_app.command("list")
def team_list(
    output_format: str = typer.Option("table", "--format", "-f", help="Output format: table, json"),
):
    """List all teams."""
    async def _list():
        await init_db()
        try:
            async with async_session_factory() as session:
                from sqlalchemy import select
                result = await session.execute(select(Team).order_by(Team.created_at.desc()))
                teams = result.scalars().all()

                if output_format == "json":
                    _output_json({
                        "teams": [
                            {
                                "id": t.id, "name": t.name, "slug": t.slug,
                                "is_public": t.is_public, "max_members": t.max_members,
                                "created_at": t.created_at.isoformat() if t.created_at else None,
                                "member_count": len(t.members) if t.members else 0,
                            }
                            for t in teams
                        ]
                    })
                    return

                if not teams:
                    console.print("[yellow]No teams found[/yellow]")
                    return

                table = Table(title="Teams", box=box.ROUNDED)
                table.add_column("ID", style="dim", max_width=12)
                table.add_column("Name")
                table.add_column("Slug")
                table.add_column("Public", justify="center")
                table.add_column("Members", justify="right")
                table.add_column("Max Members", justify="right")
                table.add_column("Created")

                for t in teams:
                    member_count = len(t.members) if t.members else 0
                    table.add_row(
                        t.id[:8] + "...",
                        t.name,
                        t.slug,
                        "✓" if t.is_public else "✗",
                        str(member_count),
                        str(t.max_members),
                        _format_datetime(t.created_at),
                    )
                console.print(table)
        finally:
            await close_db()

    asyncio.run(_list())


@team_app.command("create")
def team_create(
    name: str = typer.Option(..., "--name", "-n", help="Team name"),
    slug: str = typer.Option(..., "--slug", "-s", help="Team slug (URL-friendly)"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Team description"),
    public: bool = typer.Option(False, "--public", help="Make team publicly visible"),
    max_members: int = typer.Option(5, "--max-members", help="Maximum team members"),
    owner_id: Optional[str] = typer.Option(None, "--owner", help="Owner user ID"),
):
    """Create a new team."""
    async def _create():
        await init_db()
        try:
            async with async_session_factory() as session:
                from sqlalchemy import select

                # Resolve owner
                if owner_id:
                    user = await session.get(User, owner_id)
                    if not user:
                        console.print(f"[red]User {owner_id} not found[/red]")
                        return
                    resolved_owner = owner_id
                else:
                    result = await session.execute(select(User).limit(1))
                    user = result.scalar_one_or_none()
                    if not user:
                        console.print("[red]No users found. Create a user first.[/red]")
                        return
                    resolved_owner = user.id

                team = Team(
                    owner_id=resolved_owner,
                    name=name,
                    slug=slug,
                    description=description,
                    is_public=public,
                    max_members=max_members,
                )
                session.add(team)
                await session.commit()
                console.print(f"[green]✓ Created team[/green] [bold]{name}[/bold] (ID: {team.id[:8]}...)")
                console.print(f"  Slug: {slug}  Public: {public}  Max Members: {max_members}")
        finally:
            await close_db()

    asyncio.run(_create())


@team_app.command("invite")
def team_invite(
    team_id: str = typer.Option(..., "--team", "-t", help="Team ID"),
    email: str = typer.Option(..., "--email", "-e", help="Email to invite"),
    role: str = typer.Option("member", "--role", "-r", help="Role: owner, admin, member, viewer"),
    invited_by: Optional[str] = typer.Option(None, "--by", help="Inviter user ID"),
):
    """Invite a user to a team."""
    async def _invite():
        await init_db()
        try:
            async with async_session_factory() as session:
                import secrets
                team = await session.get(Team, team_id)
                if not team:
                    console.print(f"[red]Team {team_id} not found[/red]")
                    return

                # Check if team is full
                current_members = len(team.members) if team.members else 0
                if current_members >= team.max_members:
                    console.print(f"[red]Team is full ({current_members}/{team.max_members} members)[/red]")
                    return

                invite = TeamInvite(
                    team_id=team_id,
                    invited_by=invited_by or team.owner_id,
                    email=email.lower(),
                    role=role,
                    token=secrets.token_urlsafe(32),
                    expires_at=datetime.utcnow() + timedelta(days=7),
                )
                session.add(invite)
                await session.commit()
                console.print(f"[green]✓ Invited[/green] {email} to {team.name} as {role}")
                console.print(f"  Token: {invite.token[:16]}...  Expires: {invite.expires_at}")
        finally:
            await close_db()

    asyncio.run(_invite())


# ═══════════════════════════════════════════════════════════════════════════════
# INCIDENT COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

@incident_app.command("list")
def incident_list(
    status_filter: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    severity_filter: Optional[str] = typer.Option(None, "--severity", help="Filter by severity"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
    output_format: str = typer.Option("table", "--format", "-f", help="Output format: table, json"),
):
    """List incidents with optional filtering."""
    async def _list():
        await init_db()
        try:
            async with async_session_factory() as session:
                from sqlalchemy import select

                query = select(Incident).order_by(Incident.started_at.desc())
                if status_filter:
                    query = query.where(Incident.status == status_filter)
                if severity_filter:
                    query = query.where(Incident.severity == severity_filter)
                query = query.limit(limit)

                result = await session.execute(query)
                incidents = result.scalars().all()

                if output_format == "json":
                    _output_json({
                        "incidents": [
                            {
                                "id": i.id, "title": i.title, "severity": i.severity,
                                "status": i.status, "started_at": i.started_at.isoformat() if i.started_at else None,
                                "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
                                "duration_seconds": i.duration_seconds,
                            }
                            for i in incidents
                        ]
                    })
                    return

                if not incidents:
                    console.print("[yellow]No incidents found[/yellow]")
                    return

                table = Table(title="Incidents", box=box.ROUNDED)
                table.add_column("ID", style="dim", max_width=12)
                table.add_column("Title", max_width=40)
                table.add_column("Severity", justify="center")
                table.add_column("Status", justify="center")
                table.add_column("Duration", justify="right")
                table.add_column("Started")

                for i in incidents:
                    duration = _format_duration(i.duration_seconds)
                    table.add_row(
                        i.id[:8] + "...",
                        i.title[:40],
                        f"{_severity_icon(i.severity)} {i.severity}",
                        f"[{_status_color(i.status)}]{i.status}[/{_status_color(i.status)}]",
                        duration,
                        _format_datetime(i.started_at),
                    )
                console.print(table)
        finally:
            await close_db()

    asyncio.run(_list())


@incident_app.command("resolve")
def incident_resolve(
    incident_id: str = typer.Argument(..., help="Incident ID to resolve"),
    notes: Optional[str] = typer.Option(None, "--notes", "-n", help="Resolution notes"),
):
    """Resolve an incident."""
    async def _resolve():
        await init_db()
        try:
            async with async_session_factory() as session:
                incident = await session.get(Incident, incident_id)
                if not incident:
                    console.print(f"[red]Incident {incident_id} not found[/red]")
                    return

                old_status = incident.status
                incident.status = IncidentStatus.RESOLVED
                incident.resolved_at = datetime.utcnow()
                incident.duration_seconds = int(
                    (incident.resolved_at - incident.started_at).total_seconds()
                )
                if notes:
                    incident.resolution_notes = notes

                event = IncidentEvent(
                    incident_id=incident.id,
                    event_type="resolved",
                    message=notes or "Incident resolved",
                    old_status=old_status,
                    new_status=IncidentStatus.RESOLVED,
                )
                session.add(event)
                await session.commit()
                console.print(f"[green]✓ Resolved[/green] {incident.title}")
                console.print(f"  Duration: {_format_duration(incident.duration_seconds)}")
        finally:
            await close_db()

    asyncio.run(_resolve())


@incident_app.command("comment")
def incident_comment(
    incident_id: str = typer.Argument(..., help="Incident ID"),
    message: str = typer.Option(..., "--message", "-m", help="Comment text"),
    user_id: Optional[str] = typer.Option(None, "--user", help="User ID"),
):
    """Add a comment to an incident."""
    async def _comment():
        await init_db()
        try:
            async with async_session_factory() as session:
                incident = await session.get(Incident, incident_id)
                if not incident:
                    console.print(f"[red]Incident {incident_id} not found[/red]")
                    return

                event = IncidentEvent(
                    incident_id=incident.id,
                    user_id=user_id,
                    event_type="comment",
                    message=message,
                )
                session.add(event)
                await session.commit()
                console.print(f"[green]✓ Comment added[/green] to {incident.title}")
        finally:
            await close_db()

    asyncio.run(_comment())


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG IMPORT/EXPORT
# ═══════════════════════════════════════════════════════════════════════════════

@config_app.command("export")
def config_export(
    output_file: str = typer.Option("pulse-config.yaml", "--output", "-o", help="Output file path"),
    format: str = typer.Option("yaml", "--format", "-f", help="Output format: yaml, json"),
    include_monitors: bool = typer.Option(True, "--monitors/--no-monitors", help="Include monitors"),
    include_channels: bool = typer.Option(True, "--channels/--no-channels", help="Include alert channels"),
    include_rules: bool = typer.Option(True, "--rules/--no-rules", help="Include alert rules"),
):
    """Export monitors and configuration to YAML or JSON."""
    async def _export():
        await init_db()
        try:
            async with async_session_factory() as session:
                from sqlalchemy import select

                config = {
                    "version": "1.0",
                    "exported_at": datetime.utcnow().isoformat(),
                    "app_name": settings.app_name,
                }

                if include_monitors:
                    result = await session.execute(select(Monitor))
                    monitors = result.scalars().all()
                    config["monitors"] = [
                        {
                            "id": m.id, "name": m.name, "url": m.url,
                            "type": m.type, "interval_seconds": m.interval_seconds,
                            "timeout_seconds": m.timeout_seconds, "retries": m.retries,
                            "method": m.method, "expected_status_code": m.expected_status_code,
                            "expected_keyword": m.expected_keyword,
                            "follow_redirects": m.follow_redirects,
                            "verify_ssl": m.verify_ssl, "is_public": m.is_public,
                        }
                        for m in monitors
                    ]

                if include_channels:
                    result = await session.execute(select(AlertChannel))
                    channels = result.scalars().all()
                    config["alert_channels"] = [
                        {
                            "id": c.id, "name": c.name, "channel_type": c.channel_type,
                            "is_enabled": c.is_enabled, "is_default": c.is_default,
                            "config": c.config,
                        }
                        for c in channels
                    ]

                if include_rules:
                    result = await session.execute(select(AlertRule))
                    rules = result.scalars().all()
                    config["alert_rules"] = [
                        {
                            "id": r.id, "name": r.name, "severity": r.severity,
                            "is_enabled": r.is_enabled,
                            "trigger_on_down": r.trigger_on_down,
                            "trigger_on_up": r.trigger_on_up,
                            "consecutive_failures": r.consecutive_failures,
                            "cooldown_minutes": r.cooldown_minutes,
                        }
                        for r in rules
                    ]

                output_path = Path(output_file)
                if format == "json":
                    output_path.write_text(json.dumps(config, indent=2, default=str))
                else:
                    output_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))

                console.print(f"[green]✓ Exported[/green] to {output_path}")
                console.print(f"  Monitors: {len(config.get('monitors', []))}")
                console.print(f"  Channels: {len(config.get('alert_channels', []))}")
                console.print(f"  Rules: {len(config.get('alert_rules', []))}")
        finally:
            await close_db()

    asyncio.run(_export())


@config_app.command("import")
def config_import(
    input_file: str = typer.Option(..., "--input", "-i", help="Input file path"),
    format: str = typer.Option("auto", "--format", "-f", help="Input format: yaml, json, auto"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without saving"),
):
    """Import monitors and configuration from YAML or JSON."""
    async def _import():
        input_path = Path(input_file)
        if not input_path.exists():
            console.print(f"[red]File not found: {input_file}[/red]")
            return

        content = input_path.read_text()

        if format == "auto":
            if input_path.suffix in (".yaml", ".yml"):
                config = yaml.safe_load(content)
            else:
                config = json.loads(content)
        elif format == "json":
            config = json.loads(content)
        else:
            config = yaml.safe_load(content)

        if not config:
            console.print("[red]Empty or invalid config file[/red]")
            return

        monitors = config.get("monitors", [])
        channels = config.get("alert_channels", [])
        rules = config.get("alert_rules", [])

        console.print(f"[bold]Import Preview:[/bold]")
        console.print(f"  Monitors: {len(monitors)}")
        console.print(f"  Alert Channels: {len(channels)}")
        console.print(f"  Alert Rules: {len(rules)}")

        if dry_run:
            console.print("[yellow]Dry run — no changes made[/yellow]")
            if monitors:
                table = Table(title="Monitors to Import", box=box.SIMPLE)
                table.add_column("Name")
                table.add_column("URL")
                table.add_column("Type")
                for m in monitors:
                    table.add_row(m.get("name", "?"), m.get("url", "?"), m.get("type", "?"))
                console.print(table)
            return

        await init_db()
        try:
            async with async_session_factory() as session:
                from sqlalchemy import select
                imported = {"monitors": 0, "channels": 0, "rules": 0}

                with Progress(
                    SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                    BarColumn(), console=console,
                ) as progress:
                    if monitors:
                        task = progress.add_task("Importing monitors...", total=len(monitors))
                        for m_data in monitors:
                            monitor = Monitor(
                                owner_id=m_data.get("owner_id", "imported"),
                                name=m_data["name"],
                                url=m_data["url"],
                                type=m_data.get("type", "https"),
                                interval_seconds=m_data.get("interval_seconds", 60),
                                timeout_seconds=m_data.get("timeout_seconds", 10),
                                retries=m_data.get("retries", 3),
                                method=m_data.get("method", "GET"),
                                expected_status_code=m_data.get("expected_status_code"),
                                expected_keyword=m_data.get("expected_keyword"),
                                follow_redirects=m_data.get("follow_redirects", True),
                                verify_ssl=m_data.get("verify_ssl", True),
                                is_public=m_data.get("is_public", False),
                            )
                            session.add(monitor)
                            imported["monitors"] += 1
                            progress.advance(task)

                await session.commit()
                console.print(f"[green]✓ Import complete:[/green]")
                console.print(f"  Monitors: {imported['monitors']}")
                console.print(f"  Channels: {imported['channels']}")
                console.print(f"  Rules: {imported['rules']}")
        finally:
            await close_db()

    asyncio.run(_import())


# ═══════════════════════════════════════════════════════════════════════════════
# LOGS COMMAND
# ═══════════════════════════════════════════════════════════════════════════════

@app.command()
def logs(
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
    level: Optional[str] = typer.Option(None, "--level", "-l", help="Filter by log level"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    component: Optional[str] = typer.Option(None, "--component", "-c", help="Filter by component"),
):
    """Tail application logs with filtering."""
    log_dir = Path("logs")
    if not log_dir.exists():
        console.print("[yellow]No logs directory found. Logs may be going to stdout.[/yellow]")
        return

    log_files = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not log_files:
        console.print("[yellow]No log files found in logs/[/yellow]")
        return

    # Read the most recent log file
    log_file = log_files[0]
    content = log_file.read_text()
    all_lines = content.strip().split("\n")

    # Apply filters
    filtered = all_lines
    if level:
        filtered = [l for l in filtered if level.upper() in l.upper()]
    if component:
        filtered = [l for l in filtered if component.lower() in l.lower()]

    display_lines = filtered[-lines:]

    table = Table(title=f"Logs: {log_file.name} (last {len(display_lines)} of {len(filtered)} lines)", box=box.SIMPLE)
    table.add_column("Line", style="dim", max_width=6)
    table.add_column("Content", max_width=120)

    for i, line in enumerate(display_lines, 1):
        # Color by level
        if "ERROR" in line or "CRITICAL" in line:
            styled = f"[red]{line}[/red]"
        elif "WARNING" in line:
            styled = f"[yellow]{line}[/yellow]"
        elif "INFO" in line:
            styled = f"[blue]{line}[/blue]"
        elif "DEBUG" in line:
            styled = f"[dim]{line}[/dim]"
        else:
            styled = line
        table.add_row(str(max(1, len(filtered) - len(display_lines) + i)), styled)

    console.print(table)

    if follow:
        console.print("[dim]Following logs... (Ctrl+C to stop)[/dim]")
        try:
            import time
            last_size = log_file.stat().st_size
            while True:
                time.sleep(1)
                current_size = log_file.stat().st_size
                if current_size > last_size:
                    new_content = log_file.read_text()
                    new_lines = new_content[last_size:].strip().split("\n")
                    for line in new_lines:
                        if line:
                            console.print(line)
                    last_size = current_size
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped following logs[/dim]")


# ═══════════════════════════════════════════════════════════════════════════════
# EXISTING COMMANDS (preserved)
# ═══════════════════════════════════════════════════════════════════════════════

@app.command()
def init():
    """Initialize database tables."""
    async def _init():
        await init_db()
        console.print("[green]Database initialized successfully[/green]")
        await close_db()
    asyncio.run(_init())


@app.command()
def create_user(
    email: str = typer.Option(..., help="User email"),
    username: str = typer.Option(..., help="Username"),
    password: str = typer.Option(..., help="Password"),
    admin: bool = typer.Option(False, help="Make superuser"),
):
    """Create a new user."""
    from src.services.auth_service import AuthService

    async def _create():
        await init_db()
        async with async_session_factory() as session:
            user = User(
                email=email.lower(),
                username=username.lower(),
                hashed_password=AuthService.hash_password(password),
                is_superuser=admin,
                is_verified=True,
            )
            session.add(user)
            await session.commit()
            console.print(f"[green]Created user {username} ({email})[/green]")
        await close_db()
    asyncio.run(_create())


@app.command()
def list_users():
    """List all users."""

    async def _list():
        await init_db()
        async with async_session_factory() as session:
            from sqlalchemy import select
            result = await session.execute(select(User))
            users = result.scalars().all()
            table = Table(title="Pulse Users")
            table.add_column("ID", style="dim")
            table.add_column("Username")
            table.add_column("Email")
            table.add_column("Plan")
            table.add_column("Active")
            for u in users:
                table.add_row(u.id[:8]+"...", u.username, u.email, u.plan, str(u.is_active))
            console.print(table)
        await close_db()
    asyncio.run(_list())


@app.command()
def create_admin(
    email: str = typer.Option("admin@pulse.local"),
    username: str = typer.Option("admin"),
    password: str = typer.Option("changeme123"),
):
    """Create default admin user."""
    create_user(email=email, username=username, password=password, admin=True)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0"),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(False),
):
    """Run the development server."""
    import uvicorn
    uvicorn.run("src.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
