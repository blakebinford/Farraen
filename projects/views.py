from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render, redirect
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from organizations.decorators import require_membership

from welds.services import (
    build_weld_dashboard_chart_payload,
    get_project_weld_kpis,
)

from .models import Project, ProjectMember
from .utils import get_project_for_request, user_has_project_access

@login_required
@require_membership("GUEST")
def project_list(request, org_slug):
    qs = Project.objects.filter(org=request.org, is_archived=False)
    # Only show projects the user belongs to
    my = qs.filter(memberships__user=request.user).distinct()
    return render(request, "projects/list.html", {"org": request.org, "projects": my})

@login_required
@require_membership("MEMBER")
def project_create(request, org_slug):
    if request.method == "POST":
        name = request.POST.get("name","").strip()
        if not name:
            return render(request, "projects/create.html", {"org": request.org, "error": "Name required"})
        p = Project.objects.create(org=request.org, name=name, created_by=request.user)
        # add creator as manager
        ProjectMember.objects.create(project=p, user=request.user, role=ProjectMember.Role.MANAGER, added_by=request.user)
        return redirect("projects:project_list", org_slug=request.org.slug)
    return render(request, "projects/create.html", {"org": request.org})


@method_decorator(require_membership("GUEST"), name="dispatch")
class ProjectWeldDashboardView(TemplateView):
    template_name = "projects/project_weld_dashboard.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = get_project_for_request(
            request, request.org, kwargs.get("project_slug")
        )
        if not user_has_project_access(request.user, self.project):
            raise PermissionDenied("You do not have access to this project.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        kpis = get_project_weld_kpis(self.project)
        context.update(
            {
                "org": self.request.org,
                "project": self.project,
                "weld_kpis": kpis,
                "chart_data": build_weld_dashboard_chart_payload(kpis),
            }
        )
        return context
