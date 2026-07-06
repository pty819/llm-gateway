from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api.admin._common import (
    StatePatch,
    _audit_update,
    _count_rows,
    _delete_project_without_usage,
    _ensure_login_username_available,
    _get_or_404,
    _validate_login_username,
)
from llm_gateway.api.deps import session_dep
from llm_gateway.db.models import (
    AuditEvent,
    GatewayKey,
    ModelEntitlement,
    Project,
    ProjectMembership,
    RatePolicy,
    RequestFact,
    Subject,
    SubjectType,
    TeamMembership,
    UserSession,
    utcnow,
)
from llm_gateway.services.facts import record_audit_event
from llm_gateway.services.resource_payloads import (
    apply_model_patch,
    paginated,
    redact_gateway_key,
)
from llm_gateway.services.security import create_gateway_key, hash_password

router = APIRouter()


class SubjectCreate(BaseModel):
    name: str
    type: SubjectType
    login_username: str | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)
    notes: str | None = None


class ProjectCreate(BaseModel):
    name: str
    owner_subject_id: UUID | None = None
    notes: str | None = None


class SubjectUpdate(BaseModel):
    name: str | None = None
    login_username: str | None = None
    notes: str | None = None


class SubjectPasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=256)


class ProjectUpdate(BaseModel):
    name: str | None = None
    owner_subject_id: UUID | None = None
    notes: str | None = None


class ProjectMembershipCreate(BaseModel):
    project_id: UUID
    subject_id: UUID
    role: str = "member"


class GatewayKeyCreate(BaseModel):
    subject_id: UUID
    project_id: UUID
    name: str


@router.post("/subjects")
async def create_subject(payload: SubjectCreate, session: AsyncSession = Depends(session_dep)):
    data = payload.model_dump(exclude={"password"})
    if data.get("login_username"):
        data["login_username"] = _validate_login_username(data["login_username"])
        await _ensure_login_username_available(session, data["login_username"])
    subject = Subject(**data)
    if payload.password:
        if not subject.login_username:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="login_username_required_for_password",
            )
        subject.password_hash = hash_password(payload.password)
    session.add(subject)
    await session.flush()
    await record_audit_event(
        session,
        action="subject.create",
        resource_type="subject",
        resource_id=subject.id,
        outcome="success",
    )
    await session.commit()
    await session.refresh(subject)
    return subject


@router.get("/subjects")
async def list_subjects(
    q: str | None = Query(default=None, max_length=120),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(session_dep),
):
    stmt = select(Subject)
    if q and q.strip():
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                col(Subject.name).ilike(needle),
                col(Subject.login_username).ilike(needle),
            )
        )
    total = await _count_rows(session, select(func.count()).select_from(stmt.subquery()))
    rows = (
        (
            await session.execute(
                stmt.order_by(col(Subject.created_at).desc()).offset(offset).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return paginated(rows, total, limit, offset)


@router.patch("/subjects/{subject_id}")
async def update_subject(
    subject_id: UUID,
    payload: SubjectUpdate,
    session: AsyncSession = Depends(session_dep),
):
    subject = await _get_or_404(session, Subject, subject_id)
    if payload.login_username is not None:
        payload.login_username = _validate_login_username(payload.login_username)
        if payload.login_username:
            await _ensure_login_username_available(
                session, payload.login_username, subject_id=subject.id
            )
    apply_model_patch(subject, payload)
    await _audit_update(session, "subject.update", "subject", subject.id, payload)
    await session.commit()
    await session.refresh(subject)
    return subject


@router.patch("/subjects/{subject_id}/password")
async def reset_subject_password(
    subject_id: UUID,
    payload: SubjectPasswordReset,
    session: AsyncSession = Depends(session_dep),
):
    subject = await _get_or_404(session, Subject, subject_id)
    if not subject.login_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="subject_has_no_login_username",
        )
    subject.password_hash = hash_password(payload.new_password)
    subject.updated_at = utcnow()
    await record_audit_event(
        session,
        action="subject.password.reset",
        resource_type="subject",
        resource_id=subject.id,
        outcome="success",
    )
    await session.commit()
    return {"ok": True}


@router.patch("/subjects/{subject_id}/state")
async def set_subject_state(
    subject_id: UUID, payload: StatePatch, session: AsyncSession = Depends(session_dep)
):
    subject = await _get_or_404(session, Subject, subject_id)
    subject.state = payload.state
    subject.updated_at = utcnow()
    await record_audit_event(
        session,
        action="subject.set_state",
        resource_type="subject",
        resource_id=subject.id,
        outcome="success",
        detail={"state": payload.state.value},
    )
    await session.commit()
    return subject


@router.delete("/subjects/{subject_id}")
async def delete_subject(subject_id: UUID, session: AsyncSession = Depends(session_dep)):
    subject = await _get_or_404(session, Subject, subject_id)
    if subject.is_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cannot_delete_admin_subject",
        )

    request_count = await _count_rows(
        session,
        select(func.count(col(RequestFact.id))).where(col(RequestFact.subject_id) == subject.id),
    )
    if request_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "subject_has_usage_history",
                "request_count": request_count,
            },
        )

    owned_projects = (
        (await session.execute(select(Project).where(col(Project.owner_subject_id) == subject.id)))
        .scalars()
        .all()
    )
    for project in owned_projects:
        project_usage_count = await _count_rows(
            session,
            select(func.count(col(RequestFact.id))).where(
                col(RequestFact.project_id) == project.id
            ),
        )
        if project_usage_count:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "subject_project_has_usage_history",
                    "project_id": str(project.id),
                    "request_count": project_usage_count,
                },
            )

    await record_audit_event(
        session,
        action="subject.delete",
        resource_type="subject",
        resource_id=subject.id,
        outcome="success",
        detail={"login_username": subject.login_username, "name": subject.name},
    )
    await session.execute(delete(UserSession).where(col(UserSession.subject_id) == subject.id))
    await session.execute(
        delete(TeamMembership).where(col(TeamMembership.subject_id) == subject.id)
    )
    await session.execute(
        delete(ProjectMembership).where(col(ProjectMembership.subject_id) == subject.id)
    )
    await session.execute(
        delete(ModelEntitlement).where(col(ModelEntitlement.subject_id) == subject.id)
    )
    await session.execute(
        delete(RatePolicy).where(
            col(RatePolicy.scope) == "subject", col(RatePolicy.scope_id) == subject.id
        )
    )
    for project in owned_projects:
        await _delete_project_without_usage(session, project)
    await session.execute(delete(GatewayKey).where(col(GatewayKey.subject_id) == subject.id))
    await session.execute(
        update(AuditEvent)
        .where(col(AuditEvent.actor_subject_id) == subject.id)
        .values(actor_subject_id=None)
    )
    await session.delete(subject)
    await session.commit()
    return {"ok": True}


@router.post("/projects")
async def create_project(payload: ProjectCreate, session: AsyncSession = Depends(session_dep)):
    project = Project(**payload.model_dump())
    session.add(project)
    await session.flush()
    await record_audit_event(
        session,
        action="project.create",
        resource_type="project",
        resource_id=project.id,
        outcome="success",
    )
    await session.commit()
    await session.refresh(project)
    return project


@router.get("/projects")
async def list_projects(
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(session_dep),
):
    stmt = select(Project).order_by(col(Project.created_at).desc())
    total = await _count_rows(session, select(func.count()).select_from(Project))
    rows = (await session.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return paginated(rows, total, limit, offset)


@router.patch("/projects/{project_id}")
async def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    session: AsyncSession = Depends(session_dep),
):
    project = await _get_or_404(session, Project, project_id)
    if payload.owner_subject_id:
        await _get_or_404(session, Subject, payload.owner_subject_id)
    apply_model_patch(project, payload)
    await _audit_update(session, "project.update", "project", project.id, payload)
    await session.commit()
    await session.refresh(project)
    return project


@router.post("/project-memberships")
async def create_project_membership(
    payload: ProjectMembershipCreate, session: AsyncSession = Depends(session_dep)
):
    await _get_or_404(session, Project, payload.project_id)
    await _get_or_404(session, Subject, payload.subject_id)
    membership = ProjectMembership(**payload.model_dump())
    session.add(membership)
    await session.flush()
    await record_audit_event(
        session,
        action="project_membership.create",
        resource_type="project_membership",
        resource_id=membership.id,
        outcome="success",
    )
    await session.commit()
    await session.refresh(membership)
    return membership


@router.get("/project-memberships")
async def list_project_memberships(
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(session_dep),
):
    stmt = select(ProjectMembership).order_by(col(ProjectMembership.created_at).desc())
    total = await _count_rows(session, select(func.count()).select_from(ProjectMembership))
    rows = (await session.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return paginated(rows, total, limit, offset)


@router.post("/gateway-keys")
async def issue_gateway_key(
    payload: GatewayKeyCreate, session: AsyncSession = Depends(session_dep)
):
    await _get_or_404(session, Subject, payload.subject_id)
    await _get_or_404(session, Project, payload.project_id)
    key, raw_key = await create_gateway_key(
        session,
        subject_id=payload.subject_id,
        project_id=payload.project_id,
        name=payload.name,
    )
    await record_audit_event(
        session,
        action="gateway_key.issue",
        resource_type="gateway_key",
        resource_id=key.id,
        outcome="success",
        detail={"key_prefix": key.key_prefix},
    )
    await session.commit()
    await session.refresh(key)
    return {"key": redact_gateway_key(key), "plaintext_key": raw_key}


@router.get("/gateway-keys")
async def list_gateway_keys(
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(session_dep),
):
    stmt = select(GatewayKey).order_by(col(GatewayKey.created_at).desc())
    total = await _count_rows(session, select(func.count()).select_from(GatewayKey))
    rows = (await session.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return paginated([redact_gateway_key(item) for item in rows], total, limit, offset)


@router.patch("/gateway-keys/{gateway_key_id}/state")
async def set_gateway_key_state(
    gateway_key_id: UUID,
    payload: StatePatch,
    session: AsyncSession = Depends(session_dep),
):
    key = await _get_or_404(session, GatewayKey, gateway_key_id)
    key.state = payload.state
    key.updated_at = utcnow()
    await record_audit_event(
        session,
        action="gateway_key.set_state",
        resource_type="gateway_key",
        resource_id=key.id,
        outcome="success",
        detail={"state": payload.state.value},
    )
    await session.commit()
    return redact_gateway_key(key)
