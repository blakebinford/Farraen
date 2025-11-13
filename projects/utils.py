from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from .models import Project, ProjectMember

def get_project_for_request(request, org, project_slug):
    return get_object_or_404(Project, org=org, slug=project_slug)

def user_has_project_access(user, project) -> bool:
    return ProjectMember.objects.filter(project=project, user=user).exists()


def assert_project_not_archived(project: Project):
    if project.status == Project.Status.ARCHIVED:
        raise PermissionDenied("This project is archived and locked. Contact an administrator to unarchive.")


def ensure_membership(project: Project, user, role: str, added_by=None):
    """Ensure the given user has the specified role for the project."""
    if not user:
        return
    ProjectMember.objects.update_or_create(
        project=project,
        user=user,
        defaults={"role": role, "added_by": added_by},
    )
