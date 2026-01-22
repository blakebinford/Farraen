from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Max, Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import TemplateView

from organizations.decorators import require_membership
from organizations.models import Membership

from welds.analytics import (
    WeldLengthInfo,
    _collect_length_statistics,
    build_dashboard_analytics,
)
from welds.services import (
    build_weld_dashboard_chart_payload,
    get_project_weld_kpis,
)

from .forms import ProjectForm, ProjectInfoForm, ProjectStatusForm
from .models import Project, ProjectMember
from .permissions import (
    can_invite_project_members,
    can_view_project,
    is_org_guest,
    is_project_guest,
)
from .utils import (
    assert_project_not_archived,
    ensure_membership,
    get_project_for_request,
)
from welds.models import Weld

@login_required
@require_membership("GUEST")
def project_list(request, org_slug):
    show_archived = request.GET.get("show_archived") == "1"
    qs = Project.objects.filter(org=request.org)
    if not show_archived:
        qs = qs.exclude(status=Project.Status.ARCHIVED)
    my = qs.filter(memberships__user=request.user).distinct()
    context = {
        "org": request.org,
        "projects": my,
        "show_archived": show_archived,
    }
    return render(request, "projects/list.html", context)

@login_required
@require_membership("MEMBER")
def project_create(request, org_slug):
    if request.method == "POST":
        form = ProjectForm(request.POST, org=request.org, user=request.user)
        if form.is_valid():
            project = form.save(commit=False)
            project.org = request.org
            project.created_by = request.user
            project.updated_by = request.user
            if not project.project_manager:
                project.project_manager = request.user
            project.save()
            form.save_m2m()
            ensure_membership(
                project,
                project.project_manager,
                ProjectMember.Role.PROJECT_MANAGER,
                added_by=request.user,
            )
            ensure_membership(
                project,
                project.superintendent,
                ProjectMember.Role.SUPERINTENDENT,
                added_by=request.user,
            )
            ensure_membership(
                project,
                project.quality_manager,
                ProjectMember.Role.QUALITY_MANAGER,
                added_by=request.user,
            )
            for tech in project.quality_techs.all():
                ensure_membership(
                    project,
                    tech,
                    ProjectMember.Role.QUALITY_TECH,
                    added_by=request.user,
                )
            project.sync_role_memberships()
            ensure_membership(
                project,
                request.user,
                ProjectMember.Role.PROJECT_MANAGER,
                added_by=request.user,
            )
            messages.success(request, "Project created successfully.")
            return redirect("projects:project_list", org_slug=request.org.slug)
    else:
        form = ProjectForm(org=request.org, user=request.user)
    return render(
        request,
        "projects/create.html",
        {"org": request.org, "form": form},
    )


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
        if not can_view_project(request.user, self.project):
            raise PermissionDenied("You do not have access to this project.")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        project = self.project
        assert_project_not_archived(project)
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

        planned_inches = _parse_decimal("planned_weld_inches_per_workday")
        legacy_planned_welds = _parse_decimal("planned_welds_per_workday")

        if planned_inches is None and legacy_planned_welds is not None:
            # Backward compatibility: translate legacy weld-count planning to
            # weld inches using the current project average weld length.
            welds = list(
                Weld.objects.filter(project=project)
                .select_related("primary_welder")
                .only(
                    "weld_length_inches",
                    "primary_stencil",
                    "welder_stencil_root_hotpass",
                    "welder_stencil_fill",
                    "welder_stencil_cap",
                    "welder_stencil_repair",
                    "primary_welder__stencil",
                )
            )
            project_average, *_ = _collect_length_statistics(welds)
            if project_average and project_average > 0:
                planned_inches = (
                    legacy_planned_welds * project_average
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if planned_inches is not None:
            planned_inches = planned_inches.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        updates["planned_weld_inches_per_workday"] = planned_inches
        updates["planned_welds_per_workday"] = legacy_planned_welds

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
            project.updated_by = request.user
            project.save(update_fields=changed_fields + ["updated_by", "updated_at"])
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
                "project_is_archived": self.project.status
                == Project.Status.ARCHIVED,
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


@method_decorator(require_membership("GUEST"), name="dispatch")
class ProjectDashboardView(View):
    template_name = "projects/project_dashboard.html"

    STATUS_BADGES = {
        Project.Status.PLANNED: "bg-info text-dark",
        Project.Status.ACTIVE: "bg-success",
        Project.Status.ON_HOLD: "bg-warning text-dark",
        Project.Status.COMPLETED: "bg-primary",
        Project.Status.CANCELLED: "bg-danger",
        Project.Status.ARCHIVED: "bg-secondary",
    }

    def dispatch(self, request, *args, **kwargs):
        self.project = get_project_for_request(
            request, request.org, kwargs.get("project_slug")
        )
        if is_org_guest(request.user, request.org) or is_project_guest(
            request.user, self.project
        ):
            raise PermissionDenied(
                "Guests only have access to the Weld Log and Repair Log."
            )
        if not can_view_project(request.user, self.project):
            raise PermissionDenied("You do not have access to this project.")
        self.membership = (
            ProjectMember.objects.filter(
                project=self.project, user=request.user
            )
            .only("role")
            .first()
        )
        self.membership_role = getattr(self.membership, "role", None)
        self.can_edit_project = self._determine_can_edit_project(request.user)
        self.can_edit_status = self._determine_can_edit_status(request.user)
        self.can_manage_members = can_invite_project_members(request.user, self.project)
        return super().dispatch(request, *args, **kwargs)

    def _determine_can_edit_project(self, user):
        if not user.is_authenticated:
            return False
        if user.is_superuser or user.has_perm("projects.change_project"):
            return True
        if self.project.status == Project.Status.ARCHIVED:
            return False
        if self.membership_role in ProjectMember.Role.managerial_roles():
            return True
        return False

    def _determine_can_edit_status(self, user):
        if not user.is_authenticated:
            return False
        if user.is_superuser or user.has_perm("projects.change_project_status"):
            return True
        if self.membership_role == ProjectMember.Role.QUALITY_MANAGER:
            return True
        return False

    def get(self, request, *args, **kwargs):
        context = self._build_context(request)
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        form_type = request.POST.get("form")
        info_form = None
        status_form = None
        show_edit_panel = True

        if form_type == "project-info":
            if not self.can_edit_project:
                raise PermissionDenied("You do not have permission to edit this project.")
            if self.project.status == Project.Status.ARCHIVED:
                messages.error(
                    request,
                    "Archived projects are read-only. Contact an administrator to unarchive.",
                )
                return redirect(
                    "projects:project_dashboard",
                    org_slug=request.org.slug,
                    project_slug=self.project.slug,
                )
            info_form = ProjectInfoForm(request.POST, instance=self.project)
            if info_form.is_valid():
                project = info_form.save(commit=False)
                project.updated_by = request.user
                project.save()
                messages.success(request, "Project details updated.")
                return redirect(
                    "projects:project_dashboard",
                    org_slug=request.org.slug,
                    project_slug=self.project.slug,
                )
        elif form_type == "project-status":
            if not self.can_edit_status:
                raise PermissionDenied(
                    "You do not have permission to change the project status."
                )
            status_form = ProjectStatusForm(request.POST, instance=self.project)
            if status_form.is_valid():
                new_status = status_form.cleaned_data["status"]
                allow_archived_change = False
                if (
                    self.project.status == Project.Status.ARCHIVED
                    and new_status != Project.Status.ARCHIVED
                ):
                    if request.user.has_perm("projects.can_unarchive_project") or request.user.is_superuser:
                        allow_archived_change = True
                    else:
                        status_form.add_error(
                            "status",
                            "You do not have permission to unarchive this project.",
                        )
                if not status_form.errors:
                    project = status_form.save(commit=False)
                    project.updated_by = request.user
                    try:
                        project.save(allow_archived_change=allow_archived_change)
                    except PermissionDenied as exc:
                        status_form.add_error(None, str(exc))
                    else:
                        messages.success(request, "Project status updated.")
                        return redirect(
                            "projects:project_dashboard",
                            org_slug=request.org.slug,
                            project_slug=self.project.slug,
                        )
        else:
            return redirect(
                "projects:project_dashboard",
                org_slug=request.org.slug,
                project_slug=self.project.slug,
            )

        context = self._build_context(
            request,
            info_form=info_form,
            status_form=status_form,
            show_edit_panel=show_edit_panel,
        )
        return render(request, self.template_name, context)

    def _build_context(self, request, info_form=None, status_form=None, show_edit_panel=False):
        if info_form is None and self.can_edit_project:
            info_form = ProjectInfoForm(instance=self.project)
        if status_form is None and self.can_edit_status:
            status_form = ProjectStatusForm(instance=self.project)

        metrics = self._collect_metrics()
        recent_welds = self._recent_activity()
        status_badge_class = self.STATUS_BADGES.get(
            self.project.status, "bg-secondary"
        )

        return {
            "org": request.org,
            "project": self.project,
            "metrics": metrics,
            "recent_welds": recent_welds,
            "status_badge_class": status_badge_class,
            "can_edit_project": self.can_edit_project,
            "can_edit_status": self.can_edit_status,
            "can_manage_members": self.can_manage_members,
            "info_form": info_form,
            "status_form": status_form,
            "show_edit_panel": show_edit_panel,
        }

    def _collect_metrics(self):
        welds = Weld.objects.filter(project=self.project)
        aggregates = welds.aggregate(
            total=Count("id"),
            accepted=Count("id", filter=Q(disposition=Weld.Disposition.ACCEPTED)),
            pending=Count("id", filter=Q(disposition=Weld.Disposition.PENDING)),
            repair=Count("id", filter=Q(disposition=Weld.Disposition.REPAIR)),
            cut_out=Count("id", filter=Q(disposition=Weld.Disposition.CUT_OUT)),
            with_docs=Count(
                "id",
                filter=
                Q(material1_heat__mtr_document__isnull=False)
                | Q(material2_heat__mtr_document__isnull=False),
            ),
            last_updated=Max("updated_at"),
            last_created=Max("created_at"),
            last_weld_date=Max("date_welded"),
        )

        total = aggregates.get("total") or 0
        accepted = aggregates.get("accepted") or 0
        pending = aggregates.get("pending") or 0
        rejected = (aggregates.get("repair") or 0) + (aggregates.get("cut_out") or 0)
        with_docs = aggregates.get("with_docs") or 0
        missing_docs = max(total - with_docs, 0)

        coverage_percent = Decimal("0")
        accepted_percent = Decimal("0")
        rejected_percent = Decimal("0")
        if total:
            coverage_percent = (Decimal(with_docs) / Decimal(total)) * Decimal("100")
            accepted_percent = (Decimal(accepted) / Decimal(total)) * Decimal("100")
            rejected_percent = (Decimal(rejected) / Decimal(total)) * Decimal("100")

        last_activity_candidates = [
            aggregates.get("last_updated"),
            aggregates.get("last_created"),
        ]
        last_activity_candidates = [value for value in last_activity_candidates if value]
        last_activity = max(last_activity_candidates) if last_activity_candidates else None

        return {
            "total_welds": total,
            "accepted_welds": accepted,
            "pending_welds": pending,
            "rejected_welds": rejected,
            "accepted_percent": accepted_percent,
            "rejected_percent": rejected_percent,
            "docs_with_mtr": with_docs,
            "docs_missing": missing_docs,
            "docs_coverage_percent": coverage_percent,
            "last_weld_activity": last_activity,
            "last_weld_date": aggregates.get("last_weld_date"),
        }

    def _recent_activity(self):
        return list(
            Weld.objects.filter(project=self.project)
            .select_related("primary_welder")
            .order_by("-created_at")[:5]
        )


@login_required
@require_membership("GUEST")
def project_members(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    if is_org_guest(request.user, request.org) or is_project_guest(
        request.user, project
    ):
        raise PermissionDenied(
            "Guests only have access to the Weld Log and Repair Log."
        )
    if not can_view_project(request.user, project):
        raise PermissionDenied("You do not have access to this project.")
    if not can_invite_project_members(request.user, project):
        raise PermissionDenied("You do not have permission to manage project members.")

    org_members = (
        Membership.objects.filter(org=request.org)
        .select_related("user")
        .order_by("user__email", "user__username")
    )
    memberships = (
        ProjectMember.objects.filter(project=project)
        .select_related("user")
        .order_by("user__email", "user__username")
    )

    if request.method == "POST":
        user_id = request.POST.get("user_id")
        role = (request.POST.get("role") or ProjectMember.Role.MEMBER).strip().upper()
        allowed_roles = {
            ProjectMember.Role.PROJECT_MANAGER,
            ProjectMember.Role.SUPERINTENDENT,
            ProjectMember.Role.QUALITY_MANAGER,
            ProjectMember.Role.QUALITY_TECH,
            ProjectMember.Role.MEMBER,
            ProjectMember.Role.VIEWER,
            ProjectMember.Role.GUEST,
        }
        if role not in allowed_roles:
            return HttpResponseBadRequest("Invalid project role.")

        org_membership = get_object_or_404(Membership, org=request.org, user_id=user_id)

        if role == ProjectMember.Role.GUEST and org_membership.role != Membership.Role.GUEST:
            org_membership.role = Membership.Role.GUEST
            org_membership.save(update_fields=["role"])

        user = org_membership.user
        if role == ProjectMember.Role.PROJECT_MANAGER:
            project.project_manager = user
            project.updated_by = request.user
            project.save(update_fields=["project_manager", "updated_by", "updated_at"])
            project.sync_role_memberships()
            messages.success(request, f"Set {user.email} as Project Manager.")
        elif role == ProjectMember.Role.SUPERINTENDENT:
            project.superintendent = user
            project.updated_by = request.user
            project.save(update_fields=["superintendent", "updated_by", "updated_at"])
            project.sync_role_memberships()
            messages.success(request, f"Set {user.email} as Superintendent.")
        elif role == ProjectMember.Role.QUALITY_MANAGER:
            project.quality_manager = user
            project.updated_by = request.user
            project.save(update_fields=["quality_manager", "updated_by", "updated_at"])
            project.sync_role_memberships()
            messages.success(request, f"Set {user.email} as Quality Manager.")
        elif role == ProjectMember.Role.QUALITY_TECH:
            project.quality_techs.add(user)
            project.updated_by = request.user
            project.save(update_fields=["updated_by", "updated_at"])
            project.sync_role_memberships()
            messages.success(request, f"Added {user.email} as Quality Tech.")
        else:
            ProjectMember.objects.update_or_create(
                project=project,
                user=user,
                defaults={"role": role, "added_by": request.user},
            )
            messages.success(request, f"Added {user.email} to the project.")

        return redirect(
            "projects:project_members",
            org_slug=request.org.slug,
            project_slug=project.slug,
        )

    return render(
        request,
        "projects/members.html",
        {
            "org": request.org,
            "project": project,
            "org_members": org_members,
            "memberships": memberships,
        },
    )
