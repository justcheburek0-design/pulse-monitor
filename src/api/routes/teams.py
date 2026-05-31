
"""Team management API endpoints."""

from __future__ import annotations

from typing import Optional, List, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, ConfigDict, field_serializer
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.api.middleware.auth import get_current_user
from src.models.team import TeamRole

router = APIRouter(prefix="/teams", tags=["Teams"])



class CreateTeamRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=200, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = None


class TeamResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: Optional[str]
    max_members: int
    is_public: bool
    created_at: Optional[Any] = None

    model_config = ConfigDict(from_attributes=True)



    @field_serializer('created_at')
    def serialize_dt(self, dt, _info):
        return dt.isoformat() if dt else None

class TeamMemberResponse(BaseModel):
    id: str
    user_id: str
    username: str
    role: str
    is_active: bool
    joined_at: str

    model_config = ConfigDict(from_attributes=True)


class InviteRequest(BaseModel):
    email: str
    role: str = Field(default=TeamRole.MEMBER, pattern=r"^(owner|admin|member|viewer)$")


@router.get("", response_model=List[TeamResponse])
async def list_teams(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List teams the user belongs to."""
    from sqlalchemy import select
    from src.models.team import Team, TeamMember
    from src.models.user import User
    
    # Teams owned by user
    owned = await db.execute(select(Team).where(Team.owner_id == current_user.id))
    # Teams user is member of
    memberships = await db.execute(
        select(TeamMember).where(TeamMember.user_id == current_user.id, TeamMember.is_active == True)
    )
    member_teams = []
    for m in memberships.scalars().all():
        team = await db.get(Team, m.team_id)
        if team:
            member_teams.append(team)
    
    all_teams = list(owned.scalars().all()) + member_teams
    return [TeamResponse.model_validate(t) for t in all_teams]


@router.post("", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    request: CreateTeamRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create a new team."""
    from src.models.team import Team, TeamMember
    team = Team(
        owner_id=current_user.id,
        name=request.name,
        slug=request.slug,
        description=request.description,
        max_members=current_user.team_members_limit,
    )
    db.add(team)
    await db.flush()

    # Add owner as member
    member = TeamMember(
        team_id=team.id,
        user_id=current_user.id,
        role=TeamRole.OWNER,
    )
    db.add(member)
    await db.flush()
    return TeamResponse.model_validate(team)


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get team details."""
    from sqlalchemy import select
    from src.models.team import Team
    result = await db.execute(select(Team).where(Team.id == team_id))
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return TeamResponse.model_validate(team)


@router.get("/{team_id}/members", response_model=List[TeamMemberResponse])
async def list_members(
    team_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List team members."""
    from sqlalchemy import select
    from src.models.team import TeamMember
    from src.models.user import User
    result = await db.execute(
        select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.is_active == True)
    )
    members = result.scalars().all()
    resp = []
    for m in members:
        user = await db.get(User, m.user_id)
        resp.append(TeamMemberResponse(
            id=m.id,
            user_id=m.user_id,
            username=user.username if user else "unknown",
            role=m.role,
            is_active=m.is_active,
            joined_at=m.joined_at.isoformat() if m.joined_at else "",
        ))
    return resp


@router.post("/{team_id}/invites", status_code=status.HTTP_201_CREATED)
async def invite_member(
    team_id: str,
    request: InviteRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Invite a user to the team."""
    import secrets
    from datetime import datetime, timedelta
    from src.models.team import Team, TeamMember, TeamInvite

    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Check permission
    membership = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == current_user.id,
            TeamMember.is_active == True,
        )
    )
    member = membership.scalar_one_or_none()
    if not member or not member.can_manage_members:
        raise HTTPException(status_code=403, detail="You don't have permission to invite members")

    # Check member limit
    count = await db.execute(
        select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.is_active == True)
    )
    if len(count.scalars().all()) >= team.max_members:
        raise HTTPException(status_code=400, detail="Team member limit reached")

    invite = TeamInvite(
        team_id=team_id,
        invited_by=current_user.id,
        email=request.email,
        role=request.role,
        token=secrets.token_urlsafe(32),
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    db.add(invite)
    await db.flush()
    return {"message": f"Invitation sent to {request.email}", "invite_id": invite.id}


@router.delete("/{team_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    team_id: str,
    member_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Remove a member from the team."""
    from sqlalchemy import select
    from src.models.team import TeamMember
    membership = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == current_user.id,
            TeamMember.is_active == True,
        )
    )
    requester = membership.scalar_one_or_none()
    if not requester or not requester.can_manage_members:
        raise HTTPException(status_code=403, detail="No permission")

    target = await db.get(TeamMember, member_id)
    if not target or target.team_id != team_id:
        raise HTTPException(status_code=404, detail="Member not found")
    target.is_active = False
    await db.flush()
