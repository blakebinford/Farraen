from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.dateparse import parse_date
from django.views.generic import TemplateView

from organizations.decorators import require_membership

from welds.analytics import WeldLengthInfo, build_dashboard_analytics
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

    @staticmethod
    def _jsonify(value):
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: ProjectWeldDashboardView._jsonify(val) for key, val in value.items()}
        if isinstance(value, list):
            return [ProjectWeldDashboardView._jsonify(item) for item in value]
        if isinstance(value, WeldLengthInfo):
            return {
                "weld_id": value.weld_id,
                "length": float(value.length),
                "estimated": value.estimated,
                "source": value.source,
                "basis": value.basis,
            }
        return value

    def dispatch(self, request, *args, **kwargs):
        self.project = get_project_for_request(
            request, request.org, kwargs.get("project_slug")
        )
        if not user_has_project_access(request.user, self.project):
            raise PermissionDenied("You do not have access to this project.")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        project = self.project
        updates = {}

        def _parse_decimal(key):
            raw = request.POST.get(key, "").strip()
            if raw == "":
                return None
            try:
                return Decimal(raw)
            except (InvalidOperation, ValueError):
                return None

        def _parse_int(key):
            raw = request.POST.get(key, "").strip()
            if raw == "":
                return None
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None

        planned_per_day = _parse_decimal("planned_welds_per_workday")
        updates["planned_welds_per_workday"] = planned_per_day

        total_weld_inches = _parse_decimal("project_total_weld_inches")
        updates["project_total_weld_inches"] = total_weld_inches

        workdays_per_week = _parse_int("workdays_per_week")
        updates["workdays_per_week"] = workdays_per_week

        start_date_value = request.POST.get("planned_start_date", "").strip()
        start_date = parse_date(start_date_value) if start_date_value else None
        updates["planned_start_date"] = start_date

        changed_fields = []
        for field, value in updates.items():
            if getattr(project, field) != value:
                setattr(project, field, value)
                changed_fields.append(field)

        if changed_fields:
            project.save(update_fields=changed_fields)
            messages.success(request, "Planner inputs saved for this project.")
        else:
            messages.info(request, "No planner inputs were changed.")

        return redirect(
            "projects:weld_dashboard",
            org_slug=request.org.slug,
            project_slug=project.slug,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        kpis = get_project_weld_kpis(self.project)
        analytics = build_dashboard_analytics(self.project, {})
        analytics_json = {
            key: self._jsonify(value)
            for key, value in analytics.items()
            if key != "length_info"
        }
        context.update(
            {
                "org": self.request.org,
                "project": self.project,
                "weld_kpis": kpis,
                "chart_data": build_weld_dashboard_chart_payload(kpis),
                "analytics": analytics_json,
                "dashboard_api_url": reverse(
                    "welds:weld_dashboard_analytics",
                    kwargs={
                        "org_slug": self.request.org.slug,
                        "project_slug": self.project.slug,
                    },
                ),
                "drilldown_api_url": reverse(
                    "welds:weld_dashboard_drilldown",
                    kwargs={
                        "org_slug": self.request.org.slug,
                        "project_slug": self.project.slug,
                    },
                ),
            }
        )
        return context
