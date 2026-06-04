from enum import StrEnum
from typing import Any

from llm_gateway.db.models import ProjectMembership, Subject, TeamMembership


class ManagedRole(StrEnum):
    MEMBER = "member"
    MANAGER = "manager"


def managed_role_options() -> list[dict[str, str]]:
    return [{"value": role.value, "label": role.value} for role in ManagedRole]


def project_membership_payload(
    membership: ProjectMembership, subject: Subject
) -> dict[str, Any]:
    return {
        "id": membership.id,
        "created_at": membership.created_at,
        "updated_at": membership.updated_at,
        "project_id": membership.project_id,
        "subject_id": membership.subject_id,
        "subject_name": subject.name,
        "subject_login_username": subject.login_username,
        "role": membership.role,
    }


def team_membership_payload(
    membership: TeamMembership, subject: Subject
) -> dict[str, Any]:
    return {
        "id": membership.id,
        "created_at": membership.created_at,
        "updated_at": membership.updated_at,
        "team_id": membership.team_id,
        "subject_id": membership.subject_id,
        "subject_name": subject.name,
        "subject_login_username": subject.login_username,
        "role": membership.role,
        "state": membership.state,
    }
