"""Incident management service."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.incident import Incident, IncidentEvent, IncidentStatus, IncidentSeverity


class IncidentService:
    """CRUD and management of incidents."""

    @staticmethod
    async def create_incident(
        db: AsyncSession,
        monitor_id: str,
        title: str,
        description: Optional[str] = None,
        severity: str = IncidentSeverity.MAJOR,
        created_by: Optional[str] = None,
    ) -> Incident:
        now = datetime.utcnow()
        incident = Incident(
            monitor_id=monitor_id,
            title=title,
            description=description,
            severity=severity,
            status=IncidentStatus.INVESTIGATING,
            started_at=now,
            created_by=created_by,
        )
        db.add(incident)

        # Add creation event
        event = IncidentEvent(
            incident_id=incident.id,
            user_id=created_by,
            event_type="created",
            message=f"Investigating: {title}",
            new_status=IncidentStatus.INVESTIGATING,
        )
        db.add(event)
        await db.flush()
        return incident

    @staticmethod
    async def update_status(
        db: AsyncSession,
        incident_id: str,
        new_status: str,
        user_id: Optional[str] = None,
        message: Optional[str] = None,
    ) -> Optional[Incident]:
        incident = await db.get(Incident, incident_id)
        if not incident:
            return None

        old_status = incident.status
        incident.status = new_status

        if new_status == IncidentStatus.RESOLVED:
            incident.resolved_at = datetime.utcnow()
            incident.duration_seconds = (incident.resolved_at - incident.started_at).total_seconds()
        elif new_status == IncidentStatus.IDENTIFIED:
            incident.identified_at = datetime.utcnow()

        event = IncidentEvent(
            incident_id=incident.id,
            user_id=user_id,
            event_type="status_change",
            message=message or f"Status changed from {old_status} to {new_status}",
            old_status=old_status,
            new_status=new_status,
        )
        db.add(event)
        await db.flush()
        return incident

    @staticmethod
    async def add_comment(
        db: AsyncSession,
        incident_id: str,
        user_id: str,
        message: str,
    ) -> Optional[IncidentEvent]:
        incident = await db.get(Incident, incident_id)
        if not incident:
            return None

        event = IncidentEvent(
            incident_id=incident.id,
            user_id=user_id,
            event_type="comment",
            message=message,
        )
        db.add(event)
        await db.flush()
        return event

    @staticmethod
    async def resolve_incident(
        db: AsyncSession,
        incident_id: str,
        user_id: Optional[str] = None,
        resolution_notes: Optional[str] = None,
    ) -> Optional[Incident]:
        incident = await db.update_status(
            db, incident_id, IncidentStatus.RESOLVED, user_id,
            message=resolution_notes or "Incident resolved",
        )
        if incident and resolution_notes:
            incident.resolution_notes = resolution_notes
        await db.flush()
        return incident

    @staticmethod
    async def get_active_incidents(
        db: AsyncSession,
        monitor_id: Optional[str] = None,
    ) -> List[Incident]:
        query = select(Incident).where(Incident.status != IncidentStatus.RESOLVED)
        if monitor_id:
            query = query.where(Incident.monitor_id == monitor_id)
        query = query.order_by(Incident.started_at.desc())
        result = await db.execute(query)
        return result.scalars().all()

    @staticmethod
    async def get_incident_history(
        db: AsyncSession,
        monitor_id: str,
        limit: int = 50,
    ) -> List[Incident]:
        result = await db.execute(
            select(Incident)
            .where(Incident.monitor_id == monitor_id)
            .order_by(Incident.started_at.desc())
            .limit(limit)
        )
        return result.scalars().all()
