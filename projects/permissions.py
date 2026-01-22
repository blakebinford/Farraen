from __future__ import annotations

from organizations.models import Membership

from .models import Project, ProjectMember


def get_org_membership(user, org):
    if not user or not getattr(user, "is_authenticated", False):
        return None
    return Membership.objects.filter(org=org, user=user).first()


def get_project_membership(user, project: Project):
    if not user or not getattr(user, "is_authenticated", False):
        return None
    return ProjectMember.objects.filter(project=project, user=user).first()


def can_view_project(user, project: Project) -> bool:
    """True when the user is a project member and an org member."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if not Membership.objects.filter(org=project.org, user=user).exists():
        return False
    return ProjectMember.objects.filter(project=project, user=user).exists()


def _org_allows_edit(membership: Membership | None) -> bool:
    if not membership:
        return False
    return membership.role in {
        Membership.Role.MEMBER,
        Membership.Role.ADMIN,
        Membership.Role.OWNER,
    }


def can_edit_drive(user, project: Project) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    org_membership = get_org_membership(user, project.org)
    if not _org_allows_edit(org_membership):
        return False
    membership = get_project_membership(user, project)
    if not membership:
        return False
    return membership.role in {
        ProjectMember.Role.PROJECT_MANAGER,
        ProjectMember.Role.SUPERINTENDENT,
        ProjectMember.Role.QUALITY_MANAGER,
        ProjectMember.Role.QUALITY_TECH,
        ProjectMember.Role.MEMBER,
    }


def can_edit_welds(user, project: Project) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    org_membership = get_org_membership(user, project.org)
    if not _org_allows_edit(org_membership):
        return False
    membership = get_project_membership(user, project)
    if not membership:
        return False
    return membership.role in {
        ProjectMember.Role.QUALITY_MANAGER,
        ProjectMember.Role.QUALITY_TECH,
    }


def can_invite_project_members(user, project: Project) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    org_membership = get_org_membership(user, project.org)
    if not org_membership:
        return False
    if org_membership.role in {Membership.Role.ADMIN, Membership.Role.OWNER}:
        return True
    membership = get_project_membership(user, project)
    if not membership:
        return False
    return membership.role in ProjectMember.Role.managerial_roles()


def is_org_guest(user, org) -> bool:
    membership = get_org_membership(user, org)
    return bool(membership and membership.role == Membership.Role.GUEST)


def is_project_guest(user, project: Project) -> bool:
    membership = get_project_membership(user, project)
    return bool(membership and membership.role == ProjectMember.Role.GUEST)
