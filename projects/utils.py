from django.shortcuts import get_object_or_404
from .models import Project, ProjectMember

def get_project_for_request(request, org, project_slug):
    return get_object_or_404(Project, org=org, slug=project_slug)

def user_has_project_access(user, project) -> bool:
    return ProjectMember.objects.filter(project=project, user=user).exists()
