
"""Incident management API endpoints."""

from __future__ import annotations

from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, ConfigDict, field_serializer
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.api.middleware.auth import get_current_user
from src.models.incident import IncidentSeverity, IncidentStatus
from src.services.incident_service import IncidentService

router = APIRouter(prefix="/incidents", tags=["Incidents"])



class CreateIncidentRequest(BaseModel):
    monitor_id: str
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    severity: str = Field(default=IncidentSeverity.MAJOR, pattern=r"^(critical|major|minor|maintenance)$")


class UpdateIncidentRequest(BaseModel):
    status: Optional[str] = Field(None, pattern=r"^(investigating|identified|monitoring|resolved)$")
    title: Optional[str] = None
    description: Optional[str] = None
    root_cause: Optional[str] = None
    resolution_notes: Optional[str] = None


class AddCommentRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000)


class IncidentResponse(BaseModel):
    id: str
    monitor_id: str
    title: str
    description: Optional[str]
    severity: str
    status: str
    started_at: str
    identified_at: Optional[str]
    resolved_at: Optional[str]
    duration_seconds: Optional[int]
    root_cause: Optional[str]
    created_at: str

    model_config = ConfigDict(from_attributes=True)



    @field_serializer('started_at', 'identified_at', 'resolved_at', 'created_at')
    def serialize_dt(self, dt, _info):
        return dt.isoformat() if dt else None

class IncidentEventResponse(BaseModel):
    id: str
    event_type: str
    message: str
    old_status: Optional[str]
    new_status: Optional[str]
    created_at: str

    model_config = ConfigDict(from_attributes=True)



    @field_serializer('created_at')
    def serialize_dt(self, dt, _info):
        return dt.isoformat() if dt else None

@router.get("", response_model=List[IncidentResponse])
async def list_incidents(
    monitor_id: Optional[str] = Query(None),
    active_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List incidents."""
    if active_only:
        incidents = await IncidentService.get_active_incidents(db, monitor_id=monitor_id)
    elif monitor_id:
        incidents = await IncidentService.get_incident_history(db, monitor_id, limit=limit)
    else:
        from sqlalchemy import select
        from src.models.incident import Incident
        from src.models.monitor import Monitor
        query = (
            select(Incident)
            .join(Monitor)
            .where(Monitor.owner_id == current_user.id)
            .order_by(Incident.started_at.desc())
            .limit(limit)
        )
        result = await db.execute(query)
        incidents = result.scalars().all()
    return [IncidentResponse.model_validate(i) for i in incidents]


@router.post("", response_model=IncidentResponse, status_code=status.HTTP_201_CREATED)
async def create_incident(
    request: CreateIncidentRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create a new incident."""
    incident = await IncidentService.create_incident(
        db=db,
        monitor_id=request.monitor_id,
        title=request.title,
        description=request.description,
        severity=request.severity,
        created_by=current_user.id,
    )
    return IncidentResponse.model_validate(incident)


@router.patch("/{incident_id}", response_model=IncidentResponse)
async def update_incident(
    incident_id: str,
    request: UpdateIncidentRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Update an incident."""
    if request.status:
        incident = await IncidentService.update_status(
            db, incident_id, request.status, current_user.id
        )
    else:
        from sqlalchemy import select
        from src.models.incident import Incident
        result = await db.execute(select(Incident).where(Incident.id == incident_id))
        incident = result.scalar_one_or_none()

    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    for field in ("title", "description", "root_cause", "resolution_notes"):
        val = getattr(request, field, None)
        if val is not None:
            setattr(incident, field, val)

    await db.flush()
    return IncidentResponse.model_validate(incident)


@router.post("/{incident_id}/resolve", response_model=IncidentResponse)
async def resolve_incident(
    incident_id: str,
    request: AddCommentRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Resolve an incident."""
    from sqlalchemy import select
    from src.models.incident import Incident
    result = await db.execute(select(Incident).where(Incident.id == incident_id))
    incident = result.scalar_one_or_none()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    incident.status = IncidentStatus.RESOLVED
    from datetime import datetime
    incident.resolved_at = datetime.utcnow()
    incident.duration_seconds = (incident.resolved_at - incident.started_at).total_seconds()
    incident.resolution_notes = request.message

    event =IncidentEvent(
        incident_id=incident.id,
        user_id=current_user.id,
        event_type="resolved",
        message=f"Incident resolved: {request.message}",
        old_status=IncidentStatus.MONITORING,
        new_status=IncidentStatus.RESOLVED,
    )
    db.add(event)
    await db.flush()
    return IncidentResponse.model_validate(incident)


@router.post("/{incident_id}/comments", response_model=IncidentEventResponse)
async def add_comment(
    incident_id: str,
    request: AddCommentRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Add a comment to an incident."""
    event = await IncidentService.add_comment(db, incident_id, current_user.id, request.message)
    if not event:
        raise HTTPException(status_code=404, detail="Incident not found")
    return IncidentEventResponse.model_validate(event)


@router.get("/{incident_id}/events", response_model=List[IncidentEventResponse])
async def get_incident_events(
    incident_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get events for an incident."""
    from sqlalchemy import select
    from src.models.incident import Incident, IncidentEvent
    result = await db.execute(
        select(IncidentEvent)
        .where(IncidentEvent.incident_id == incident_id)
        .order_by(IncidentEvent.created_at)
    )
    events = result.scalars().all()
    return [IncidentEventResponse.model_validate(e) for e in events]
