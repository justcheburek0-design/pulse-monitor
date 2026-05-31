"""Dashboard and widget management service."""

from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.dashboard import Dashboard, DashboardWidget, WidgetType


class DashboardService:
    """CRUD operations for dashboards and widgets."""

    @staticmethod
    async def create_dashboard(
        db: AsyncSession,
        user_id: str,
        name: str,
        slug: str,
        description: Optional[str] = None,
        columns: int = 3,
        theme: str = "dark",
        team_id: Optional[str] = None,
    ) -> Dashboard:
        dashboard = Dashboard(
            user_id=user_id,
            team_id=team_id,
            name=name,
            slug=slug,
            description=description,
            columns=columns,
            theme=theme,
        )
        db.add(dashboard)
        await db.flush()
        return dashboard

    @staticmethod
    async def get_user_dashboards(
        db: AsyncSession,
        user_id: str,
    ) -> List[Dashboard]:
        result = await db.execute(
            select(Dashboard).where(Dashboard.user_id == user_id).order_by(Dashboard.created_at.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def get_dashboard(
        db: AsyncSession,
        dashboard_id: str,
        user_id: str,
    ) -> Optional[Dashboard]:
        result = await db.execute(
            select(Dashboard).where(Dashboard.id == dashboard_id, Dashboard.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_dashboard_by_slug(
        db: AsyncSession,
        slug: str,
        user_id: str,
    ) -> Optional[Dashboard]:
        result = await db.execute(
            select(Dashboard).where(Dashboard.slug == slug, Dashboard.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_dashboard(db: AsyncSession, dashboard: Dashboard, **kwargs) -> Dashboard:
        allowed = {"name", "slug", "description", "columns", "theme", "is_public", "public_slug", "refresh_interval_seconds"}
        for key, value in kwargs.items():
            if key in allowed and value is not None:
                setattr(dashboard, key, value)
        await db.flush()
        return dashboard

    @staticmethod
    async def delete_dashboard(db: AsyncSession, dashboard: Dashboard) -> None:
        await db.delete(dashboard)
        await db.flush()

    # ── Widgets ───────────────────────────────────────────────────────────

    @staticmethod
    async def add_widget(
        db: AsyncSession,
        dashboard_id: str,
        name: str,
        widget_type: str,
        position: int = 0,
        width: int = 1,
        height: int = 1,
        config: Optional[dict] = None,
        monitor_ids: Optional[list] = None,
    ) -> DashboardWidget:
        widget = DashboardWidget(
            dashboard_id=dashboard_id,
            name=name,
            widget_type=widget_type,
            position=position,
            width=width,
            height=height,
            config=config,
            monitor_ids=monitor_ids,
        )
        db.add(widget)
        await db.flush()
        return widget

    @staticmethod
    async def update_widget(db: AsyncSession, widget: DashboardWidget, **kwargs) -> DashboardWidget:
        allowed = {"name", "widget_type", "position", "width", "height", "config", "monitor_ids"}
        for key, value in kwargs.items():
            if key in allowed and value is not None:
                setattr(widget, key, value)
        await db.flush()
        return widget

    @staticmethod
    async def delete_widget(db: AsyncSession, widget: DashboardWidget) -> None:
        await db.delete(widget)
        await db.flush()

    @staticmethod
    async def get_dashboard_with_data(
        db: AsyncSession,
        dashboard_id: str,
        user_id: str,
    ) -> Optional[dict]:
        """Get dashboard with all widget data populated."""
        dashboard = await DashboardService.get_dashboard(db, dashboard_id, user_id)
        if not dashboard:
            return None

        widgets_data = []
        for widget in dashboard.widgets:
            widget_data = {
                "id": widget.id,
                "name": widget.name,
                "type": widget.widget_type,
                "position": widget.position,
                "width": widget.width,
                "height": widget.height,
                "config": widget.config,
                "data": await DashboardService._get_widget_data(db, widget),
            }
            widgets_data.append(widget_data)

        return {
            "id": dashboard.id,
            "name": dashboard.name,
            "slug": dashboard.slug,
            "description": dashboard.description,
            "columns": dashboard.columns,
            "theme": dashboard.theme,
            "widgets": widgets_data,
        }

    @staticmethod
    async def _get_widget_data(db: AsyncSession, widget: DashboardWidget) -> dict:
        """Get data for a specific widget type."""
        from src.models.monitor import Monitor, MonitorCheck, MonitorStatus

        monitor_ids = widget.monitor_ids

        if widget.widget_type == WidgetType.STATUS_PIECE:
            if monitor_ids:
                monitors = []
                for mid in monitor_ids:
                    m = await db.get(Monitor, mid)
                    if m:
                        monitors.append(m)
            else:
                result = await db.execute(select(Monitor).where(Monitor.is_active == True).limit(1))
                monitors = result.scalars().all()

            if monitors:
                return {
                    "monitor": {"id": monitors[0].id, "name": monitors[0].name},
                    "status": monitors[0].status,
                    "uptime": monitors[0].uptime_percentage,
                    "response_time": monitors[0].avg_response_time_ms,
                }
            return {"status": "unknown"}

        elif widget.widget_type == WidgetType.STATS_OVERVIEW:
            total_result = await db.execute(select(func.count()).select_from(Monitor))
            up_result = await db.execute(
                select(func.count()).select_from(Monitor).where(Monitor.status == MonitorStatus.UP)
            )
            down_result = await db.execute(
                select(func.count()).select_from(Monitor).where(Monitor.status == MonitorStatus.DOWN)
            )
            total = total_result.scalar_one()
            up = up_result.scalar_one()
            down = down_result.scalar_one()
            return {"total": total, "up": up, "down": down, "uptime": round((up / total) * 100, 1) if total else 100}

        elif widget.widget_type == WidgetType.UPTIME_BAR:
            if monitor_ids:
                checks = []
                for mid in monitor_ids[:1]:
                    from datetime import datetime, timedelta
                    since = datetime.utcnow() - timedelta(days=30)
                    result = await db.execute(
                        select(MonitorCheck).where(
                            MonitorCheck.monitor_id == mid,
                            MonitorCheck.checked_at >= since,
                        ).order_by(MonitorCheck.checked_at.desc()).limit(90)
                    )
                    checks = result.scalars().all()
            else:
                checks = []

            return {
                "bars": [
                    {"date": c.checked_at.isoformat(), "up": c.is_up, "response_ms": c.response_time_ms}
                    for c in reversed(checks)
                ]
            }

        elif widget.widget_type == WidgetType.MONITOR_LIST:
            if monitor_ids:
                monitors = []
                for mid in monitor_ids:
                    m = await db.get(Monitor, mid)
                    if m:
                        monitors.append(m)
            else:
                result = await db.execute(
                    select(Monitor).where(Monitor.is_active == True).order_by(Monitor.name).limit(50)
                )
                monitors = result.scalars().all()

            return {
                "monitors": [
                    {
                        "id": m.id, "name": m.name, "status": m.status,
                        "uptime": m.uptime_percentage, "response_time": m.avg_response_time_ms,
                        "url": m.url, "type": m.type,
                    }
                    for m in monitors
                ]
            }

        return {}
