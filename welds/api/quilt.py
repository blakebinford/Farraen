from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from django.core.exceptions import PermissionDenied
from django.db import models
from django.db.models import Count, Max, Q, QuerySet
from django.urls import reverse
from django.utils import timezone

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from organizations.models import Membership, Organization
from organizations.decorators import ROLE_ORDER
from projects.models import Project, ProjectMember

from ..models import MaterialHeatDraft, QuiltQueryLog, Weld

try:  # Optional repair model import (newer deployments only)
    from ..models import Repair as RepairModel  # type: ignore
except (ImportError, AttributeError):  # pragma: no cover - optional dependency
    RepairModel = None

try:
    from ..models import WeldRepair
except ImportError:  # pragma: no cover - WeldRepair exists in production, but guard for tests
    WeldRepair = None  # type: ignore

from .serializers import MAX_EXPORT_ROWS, QuiltQuerySerializer

logger = logging.getLogger(__name__)
metrics_logger = logging.getLogger("welds.quilt.metrics")
# MTR records can include confidential specifications; any future LLM calls
# should default to Azure/private deployments to honour confidentiality notes.
LARGE_DATASET_THRESHOLD = 50000
RATE_LIMIT_WINDOW = timedelta(minutes=1)
RATE_LIMIT_PER_USER = 60


@dataclass
class QueryScope:
    org: Organization
    project: Project | None
    helper_used: str


class RateLimitExceeded(Exception):
    pass


class RepairDetection:
    """Strategy helper for counting repaired welds defensively."""

    def __init__(self) -> None:
        self.strategy = "unknown"
        self.related_name: str | None = None
        self._select_strategy()

    def _select_strategy(self) -> None:
        if RepairModel is not None:
            self.strategy = "repair_model"
            weld_field = RepairModel._meta.get_field("weld")
            self.related_name = weld_field.related_name or f"{RepairModel._meta.model_name}_set"
            return

        weld_model = Weld
        if hasattr(weld_model, "repair_count"):
            try:
                field = weld_model._meta.get_field("repair_count")
            except Exception:  # pragma: no cover - defensive
                field = None
            if isinstance(field, models.IntegerField):
                self.strategy = "weld_repair_count_field"
                return
        if hasattr(weld_model, "repairs"):
            self.strategy = "repairs_relation"
            self.related_name = "repairs"
            return
        if hasattr(weld_model, "status"):
            self.strategy = "status_field"
            return
        self.strategy = "unknown"

    def _build_repair_filter(self, prefix: str, filters: dict[str, Any]) -> Q | None:
        clauses: list[Q] = []
        start = filters.get("start_date")
        end = filters.get("end_date")
        if start:
            clauses.append(
                Q(**{f"{prefix}repair_date__gte": start})
                | Q(**{f"{prefix}flagged_at__gte": start})
            )
        if end:
            clauses.append(
                Q(**{f"{prefix}repair_date__lte": end})
                | Q(**{f"{prefix}flagged_at__lte": end})
            )
        if not clauses:
            return None
        combined = clauses[0]
        for clause in clauses[1:]:
            combined &= clause
        return combined

    # --- queryset helpers -------------------------------------------------
    def annotate_repaired_welds(
        self,
        welds_qs: QuerySet[Weld],
        filters: dict[str, Any],
    ) -> QuerySet[Weld]:
        if self.strategy in {"repair_model", "repairs_relation"} and self.related_name:
            relation = self.related_name
            count_filter = self._build_repair_filter(f"{relation}__", filters)
            count_kwargs = {"distinct": True}
            if count_filter is not None:
                count_kwargs["filter"] = count_filter
            welds_qs = welds_qs.filter(**{f"{relation}__isnull": False})
            welds_qs = welds_qs.annotate(
                repairs_count=Count(relation, **count_kwargs),
                last_repair_date=Max(f"{relation}__repair_date"),
                last_flagged_date=Max(f"{relation}__flagged_at"),
            )
            return welds_qs.distinct()

        if self.strategy == "weld_repair_count_field":
            return welds_qs.filter(repair_count__gt=0).annotate(
                repairs_count=models.F("repair_count"),
                last_repair_date=models.Value(None, output_field=models.DateField()),
                last_flagged_date=models.Value(None, output_field=models.DateField()),
            )

        if self.strategy == "status_field":
            statuses = {"repaired", "rework", "repair"}
            return welds_qs.filter(status__in=statuses).annotate(
                repairs_count=models.Value(1, output_field=models.IntegerField()),
                last_repair_date=models.Value(None, output_field=models.DateField()),
                last_flagged_date=models.Value(None, output_field=models.DateField()),
            )

        # Unknown strategy – fail closed by returning empty queryset.
        return welds_qs.none()

    def repair_count(self, welds_qs: QuerySet[Weld], filters: dict[str, Any]) -> int:
        return self.annotate_repaired_welds(welds_qs, filters).count()

    def top_welders(self, welds_qs: QuerySet[Weld], filters: dict[str, Any], top_n: int):
        if self.strategy in {"repair_model", "repairs_relation"} and self.related_name and WeldRepair:
            repairs_qs = WeldRepair.objects.filter(weld__in=welds_qs.values("pk"))
            repairs_qs = _apply_repair_filters(repairs_qs, filters)
            return (
                repairs_qs.values("weld__primary_welder_id", "weld__primary_welder__name")
                .annotate(repairs=Count("id"))
                .order_by("-repairs", "weld__primary_welder__name")[:top_n]
            )
        if self.strategy == "weld_repair_count_field":
            return (
                welds_qs.exclude(primary_welder_id__isnull=True)
                .values("primary_welder_id", "primary_welder__name")
                .annotate(repairs=Count("id"))
                .order_by("-repairs", "primary_welder__name")[:top_n]
            )
        if self.strategy == "status_field":
            return (
                welds_qs.exclude(primary_welder_id__isnull=True)
                .values("primary_welder_id", "primary_welder__name")
                .annotate(repairs=Count("id"))
                .order_by("-repairs", "primary_welder__name")[:top_n]
            )
        return Weld.objects.none()


def _client_ip(request) -> str | None:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _infer_intent(query_text: str | None, explicit: str | None) -> str:
    if explicit:
        return explicit
    if not query_text:
        return "repairs_summary"
    lowered = query_text.lower()
    if "export" in lowered:
        return "export_csv"
    if "top" in lowered and "welder" in lowered:
        return "kpi"
    if "repair rate" in lowered or "kpi" in lowered:
        return "kpi"
    if "list" in lowered or "show" in lowered:
        return "list_welds"
    return "repairs_summary"


def _apply_weld_filters(qs: QuerySet[Weld], filters: dict[str, Any]) -> QuerySet[Weld]:
    start = filters.get("start_date")
    end = filters.get("end_date")
    if start:
        qs = qs.filter(Q(weld_date__gte=start) | Q(date_welded__gte=start))
    if end:
        qs = qs.filter(Q(weld_date__lte=end) | Q(date_welded__lte=end))
    if filters.get("welder_id"):
        qs = qs.filter(primary_welder_id=filters["welder_id"])
    if filters.get("wps"):
        wps = filters["wps"]
        qs = qs.filter(
            Q(wps_document__number__icontains=wps)
            | Q(wps_document__title__icontains=wps)
            | Q(wps_document__name__icontains=wps)
        )
    return qs


def _apply_repair_filters(qs, filters: dict[str, Any]):
    start = filters.get("start_date")
    end = filters.get("end_date")
    if start:
        qs = qs.filter(Q(repair_date__gte=start) | Q(flagged_at__gte=start))
    if end:
        qs = qs.filter(Q(repair_date__lte=end) | Q(flagged_at__lte=end))
    if filters.get("welder_id"):
        qs = qs.filter(weld__primary_welder_id=filters["welder_id"])
    if filters.get("wps"):
        wps = filters["wps"]
        qs = qs.filter(
            Q(weld__wps_document__number__icontains=wps)
            | Q(weld__wps_document__title__icontains=wps)
            | Q(weld__wps_document__name__icontains=wps)
        )
    return qs


def _roles_at_least(min_role: str) -> list[str]:
    if min_role not in ROLE_ORDER:
        raise PermissionDenied("Invalid role threshold")
    start_index = ROLE_ORDER.index(min_role)
    return ROLE_ORDER[start_index:]


def _ensure_org_role(user, org: Organization, min_role: str) -> None:
    if user.is_superuser or org.owner_id == user.id:
        return
    membership = Membership.objects.filter(org=org, user=user).only("role").first()
    if not membership:
        raise PermissionDenied("You do not have access to this organization.")
    if ROLE_ORDER.index(membership.role) < ROLE_ORDER.index(min_role):
        raise PermissionDenied("Insufficient role.")


def _resolve_scope(user, payload: dict[str, Any], *, min_role: str | None = None) -> QueryScope:
    org_id = payload.get("org_id")
    project_id = payload.get("project_id")

    project: Project | None = None
    org: Organization | None = None

    helper_used = ""

    if project_id:
        project = Project.objects.select_related("org").get(pk=project_id)
        org = project.org

    if org_id:
        org = Organization.objects.get(pk=org_id)
        if project and project.org_id != org.id:
            raise PermissionDenied("Project does not belong to that organisation")

    if not org:
        raise PermissionDenied("An org_id or project_id is required")

    helper_used = _enforce_scope_permissions(user, org, project)
    if min_role:
        _ensure_org_role(user, org, min_role)
    return QueryScope(org=org, project=project, helper_used=helper_used)


def _enforce_scope_permissions(user, org: Organization, project: Project | None) -> str:
    helper_used = None

    if project and hasattr(project, "has_member"):
        if project.has_member(user):  # type: ignore[attr-defined]
            helper_used = "project.has_member"
    if not helper_used and hasattr(org, "has_member"):
        if org.has_member(user):  # type: ignore[attr-defined]
            helper_used = "org.has_member"
    if not helper_used and hasattr(org, "members"):
        try:
            members_rel = getattr(org, "members")
            if members_rel.filter(pk=user.pk).exists():  # type: ignore[call-arg]
                helper_used = "Organization.members"
        except Exception:  # pragma: no cover - guard against unexpected managers
            helper_used = None

    if not helper_used:
        if user.is_superuser:
            helper_used = "user.is_superuser"
        elif org.owner_id == user.id:
            helper_used = "org.owner"
        elif project and project.created_by_id == user.id:
            helper_used = "project.created_by"
        else:
            project_membership = False
            if project:
                project_membership = ProjectMember.objects.filter(
                    project=project, user=user
                ).exists()
            if project_membership:
                helper_used = "project.memberships"
            elif Membership.objects.filter(org=org, user=user).exists():
                helper_used = "org.memberships"

    if not helper_used:
        logger.info(
            "QUILT permission denied via fallback",
            extra={"user_id": user.id, "org_id": org.id, "project_id": getattr(project, "id", None)},
        )
        raise PermissionDenied("You do not have access to this scope")

    logger.info(
        "QUILT permission helper used",
        extra={
            "helper": helper_used,
            "user_id": user.id,
            "org_id": org.id,
            "project_id": getattr(project, "id", None),
        },
    )
    return helper_used


def _log_query(user, scope: QueryScope, intent: str, filters: dict[str, Any], request) -> None:
    serialised_filters: dict[str, Any] = {}
    for key, value in filters.items():
        if isinstance(value, (date, datetime)):
            serialised_filters[key] = value.isoformat()
        else:
            serialised_filters[key] = value
    QuiltQueryLog.objects.create(
        user=user,
        org=scope.org,
        project=scope.project,
        intent=intent,
        filters=serialised_filters,
        ip_address=_client_ip(request),
    )


def _check_rate_limit(user, request) -> None:
    window_start = timezone.now() - RATE_LIMIT_WINDOW
    recent = QuiltQueryLog.objects.filter(
        user=user,
        created_at__gte=window_start,
    ).count()
    if recent >= RATE_LIMIT_PER_USER:
        metrics_logger.warning(
            "QUILT rate limit exceeded",
            extra={"user_id": user.id, "count": recent},
        )
        raise RateLimitExceeded("Rate limit exceeded. Try again in a minute.")


def _parser_confidence_map(welds: Iterable[Weld]) -> dict[int, float | None]:
    file_ids: set[int] = set()
    for weld in welds:
        for attr in ("material1_heat", "material2_heat"):
            heat = getattr(weld, attr, None)
            if heat and heat.mtr_document_id:
                file_ids.add(heat.mtr_document_id)
    if not file_ids:
        return {}
    drafts = (
        MaterialHeatDraft.objects.filter(file_node_id__in=file_ids)
        .order_by("file_node_id", "-parsed_at")
        .only("file_node_id", "confidence", "raw_payload")
    )
    confidence_map: dict[int, float | None] = {}
    for draft in drafts:
        if draft.file_node_id in confidence_map:
            continue
        confidence = draft.confidence
        if confidence is None and isinstance(draft.raw_payload, dict):
            confidence = draft.raw_payload.get("confidence")  # MTR parsing stores payloads (docs/mtr_parsing.md)
        if confidence is not None:
            try:
                confidence_map[draft.file_node_id] = float(confidence)
            except (TypeError, ValueError):  # pragma: no cover - defensive for malformed payloads
                confidence_map[draft.file_node_id] = None
        else:
            confidence_map[draft.file_node_id] = None
    return confidence_map


def _material_sources_for_weld(weld: Weld, confidence_map: dict[int, float | None]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    org_slug = weld.project.org.slug
    project_slug = weld.project.slug
    try:
        weld_url = reverse(
            "welds:weld_log",
            kwargs={"org_slug": org_slug, "project_slug": project_slug},
        ) + f"?weld={weld.id}"
    except Exception:  # pragma: no cover - fallback when named route absent
        weld_url = f"/o/{org_slug}/projects/{project_slug}/welds/{weld.id}/"
    sources.append({"type": "weld", "id": weld.id, "url": weld_url})

    for heat_attr in ("material1_heat", "material2_heat"):
        heat = getattr(weld, heat_attr, None)
        if not heat or not heat.mtr_document_id:
            continue
        try:
            file_url = reverse(
                "drive_file",
                kwargs={
                    "org_slug": org_slug,
                    "project_slug": project_slug,
                    "file_id": heat.mtr_document_id,
                },
            )
        except Exception:  # pragma: no cover - fallback when route unavailable
            file_url = f"/o/{org_slug}/p/{project_slug}/drive/file/{heat.mtr_document_id}/"
        payload = {"type": "file", "id": heat.mtr_document_id, "url": file_url}
        confidence = confidence_map.get(heat.mtr_document_id)
        if confidence is not None:
            payload["parser_confidence"] = confidence
        sources.append(payload)
    return sources


def _serialise_weld(weld: Weld, confidence_map: dict[int, float | None]) -> dict[str, Any]:
    last_repair_date = getattr(weld, "last_repair_date", None) or getattr(
        weld, "last_flagged_date", None
    )
    welder_payload = None
    if weld.primary_welder:
        welder_payload = {
            "id": weld.primary_welder.id,
            "name": weld.primary_welder.name,
            "profile_url": f"/o/{weld.project.org.slug}/welders/{weld.primary_welder.id}/",
        }
    sources = _material_sources_for_weld(weld, confidence_map)
    weld_url = next((src["url"] for src in sources if src.get("type") == "weld"), None)
    return {
        "id": weld.id,
        "identifier": weld.weld_id,
        "status": getattr(weld, "disposition", None) or getattr(weld, "status", None),
        "repairs_count": getattr(weld, "repairs_count", 0),
        "last_repair_date": last_repair_date.isoformat() if last_repair_date else None,
        "welder": welder_payload,
        "weld_url": weld_url,
        "sources": sources,
    }


def _top_welders_payload(rows: Iterable[dict[str, Any]], org_slug: str) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for row in rows:
        welder_id = row.get("weld__primary_welder_id") or row.get("primary_welder_id")
        name = row.get("weld__primary_welder__name") or row.get("primary_welder__name")
        if not welder_id or not name:
            continue
        payload.append(
            {
                "welder_id": welder_id,
                "name": name,
                "repairs": row.get("repairs", 0),
                "profile_url": f"/o/{org_slug}/welders/{welder_id}/",
            }
        )
    return payload


def _build_answer(
    scope: QueryScope,
    filters: dict[str, Any],
    total_welds: int,
    repaired_welds: int,
    repair_rate: float,
    top_welders: list[dict[str, Any]],
    meta: dict[str, Any],
) -> str:
    period = ""
    start = filters.get("start_date")
    end = filters.get("end_date")
    if start and end:
        period = f" ({start:%b %d, %Y} – {end:%b %d, %Y})"
    elif start:
        period = f" (since {start:%b %d, %Y})"
    elif end:
        period = f" (through {end:%b %d, %Y})"

    scope_label = scope.project.name if scope.project else scope.org.name
    if total_welds == 0:
        return f"No weld production recorded for {scope_label}{period}."

    lines = [
        f"In {scope_label}{period}: {total_welds} total welds, {repaired_welds} repaired",
        f"→ repair rate {repair_rate * 100:.1f}%.",
    ]
    if top_welders:
        welder_summary = ", ".join(
            f"{welder['name']} ({welder['repairs']} repairs)" for welder in top_welders
        )
        lines.append(f"Top {len(top_welders)} welders: {welder_summary}.")
    if meta.get("total", 0) > meta.get("returned", 0):
        lines.append(
            f"Showing {meta['returned']} of {meta['total']}. Use 'Show more' for the next page."
        )
    if meta.get("export_limited"):
        lines.append("Export limited to 100 rows. Use Background Export for larger datasets.")
    return " ".join(lines)


class QuiltQueryView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        start_time = timezone.now()
        serializer = QuiltQuerySerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            scope = _resolve_scope(request.user, data, min_role=Membership.Role.VIEWER)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        except Organization.DoesNotExist:
            return Response({"error": "Organization not found."}, status=status.HTTP_404_NOT_FOUND)
        except PermissionDenied as exc:
            return Response({"error": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        try:
            _check_rate_limit(request.user, request)
        except RateLimitExceeded as exc:
            return Response({"error": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        filters = data["filters"]
        per_page = data["per_page"]
        page = data["page"]
        top_n = data["top_n"]
        intent = _infer_intent(data.get("query"), data.get("intent"))

        if intent == "export_csv":
            requested_raw = request.data.get("per_page")
            requested_per_page = None
            try:
                if requested_raw is not None:
                    requested_per_page = int(requested_raw)
            except (TypeError, ValueError):
                requested_per_page = None
            if requested_per_page and requested_per_page > per_page:
                per_page = requested_per_page
            per_page = max(1, min(per_page, MAX_EXPORT_ROWS))

        # Build base queryset scoped by organisation/project.
        welds_qs = Weld.objects.select_related(
            "project",
            "project__org",
            "primary_welder",
            "material1_heat",
            "material1_heat__mtr_document",
            "material2_heat",
            "material2_heat__mtr_document",
        ).filter(project__org=scope.org)
        if scope.project:
            welds_qs = welds_qs.filter(project=scope.project)
        welds_qs = _apply_weld_filters(welds_qs, filters)

        total_welds = welds_qs.distinct().count()
        if total_welds > LARGE_DATASET_THRESHOLD and not request.user.is_staff:
            metrics_logger.warning(
                "QUILT query aborted due to dataset size",
                extra={
                    "user_id": request.user.id,
                    "org_id": scope.org.id,
                    "project_id": getattr(scope.project, "id", None),
                    "total_welds": total_welds,
                },
            )
            return Response(
                {
                    "error": "Query matches more than 50000 welds. Please apply additional filters to narrow the scope.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        detector = RepairDetection()
        repaired_qs = detector.annotate_repaired_welds(welds_qs, filters)
        total_matches = repaired_qs.count()
        total_repaired = total_matches
        repair_rate = (total_repaired / total_welds) if total_welds else 0.0

        order_fields = ["-last_repair_date", "-last_flagged_date", "-id"]
        repaired_qs = repaired_qs.order_by(*order_fields)

        export_limited = False
        export_message = None
        if intent == "export_csv":
            effective_per_page = min(per_page, MAX_EXPORT_ROWS)
            if total_matches > MAX_EXPORT_ROWS:
                export_limited = True
                offset = 0
                slice_end = MAX_EXPORT_ROWS
            else:
                offset = (page - 1) * effective_per_page
                slice_end = offset + effective_per_page
            repaired_qs = repaired_qs[offset:slice_end]
            per_page = effective_per_page
            if export_limited:
                page = 1
        else:
            offset = (page - 1) * per_page
            repaired_qs = repaired_qs[offset : offset + per_page]

        weld_list = list(repaired_qs)
        confidence_map = _parser_confidence_map(weld_list)
        weld_items = [_serialise_weld(weld, confidence_map) for weld in weld_list]

        top_rows = detector.top_welders(welds_qs, filters, top_n)
        top_welders = _top_welders_payload(top_rows, scope.org.slug)

        meta: dict[str, Any] = {
            "page": page,
            "per_page": min(per_page, MAX_EXPORT_ROWS) if intent == "export_csv" else per_page,
            "returned": len(weld_items),
            "total": total_matches,
            "total_welds": total_welds,
        }
        if data.get("per_page_coerced"):
            meta["per_page_capped"] = True
        if export_limited:
            meta["export_limited"] = True
            export_message = "Export limited to 100 rows. Use Background Export for larger datasets."
        if intent == "export_csv" and meta["returned"] < total_matches:
            meta.setdefault("export_limited", True)

        answer = _build_answer(scope, filters, total_welds, total_repaired, repair_rate, top_welders, meta)

        sources = []
        seen = set()
        for weld in weld_items:
            for source in weld.get("sources", []):
                key = (source.get("type"), source.get("id"))
                if key in seen:
                    continue
                seen.add(key)
                sources.append(source)

        _log_query(request.user, scope, intent, filters, request)

        duration = timezone.now() - start_time
        metrics_logger.info(
            "QUILT query executed",
            extra={
                "duration_ms": duration.total_seconds() * 1000,
                "repair_strategy": detector.strategy,
                "user_id": request.user.id,
                "org_id": scope.org.id,
                "project_id": getattr(scope.project, "id", None),
            },
        )

        response_payload = {
            "answer": answer,
            "kpi": {
                "repair_rate": repair_rate,
                "total_welds": total_welds,
                "repaired_welds": total_repaired,
                "top_welders": top_welders,
            },
            "welds": weld_items,
            "meta": meta,
            "sources": sources,
        }
        if export_message:
            response_payload["message"] = export_message
            response_payload.setdefault("meta", {}).setdefault("notes", []).append(export_message)
            # TODO(Phase 2): enqueue Celery background export job when export_limited is True.

        if not weld_items and total_repaired == 0:
            response_payload["answer"] = (
                "No repaired welds found for that range. Try refining the filters or widening the dates."
            )

        return Response(response_payload)


class QuiltSourcesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        allowed_roles = _roles_at_least(Membership.Role.VIEWER)
        memberships = (
            Membership.objects.filter(user=request.user, role__in=allowed_roles)
            .select_related("org")
            .order_by("org__name")
        )
        if not memberships.exists():
            return Response({"error": "Insufficient role."}, status=status.HTTP_403_FORBIDDEN)
        allowed_org_ids = [membership.org_id for membership in memberships]
        project_memberships = (
            ProjectMember.objects.filter(user=request.user, project__org_id__in=allowed_org_ids)
            .select_related("project", "project__org")
            .order_by("project__org__name", "project__name")
        )

        project_map: dict[int, list[dict[str, Any]]] = {}
        for membership in project_memberships:
            project = membership.project
            project_map.setdefault(project.org_id, []).append(
                {
                    "id": project.id,
                    "name": project.name,
                    "slug": project.slug,
                }
            )

        org_payload = []
        for membership in memberships:
            org_payload.append(
                {
                    "id": membership.org.id,
                    "name": membership.org.name,
                    "slug": membership.org.slug,
                    "projects": project_map.get(membership.org.id, []),
                }
            )

        return Response({"organizations": org_payload})


__all__ = ["QuiltQueryView", "QuiltSourcesView"]
