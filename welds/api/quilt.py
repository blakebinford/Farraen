"""QUILT (Quality Integrate Learning Tool) API endpoints.

Phase 1 endpoints provide summary analytics for weld repairs with strict
permission checks. The API intentionally keeps the contract small so it can be
used both by the in-app widget and future automation. All responses are scoped
by the requesting user's memberships:

* Users must be authenticated.
* If a project_id is supplied the user must be a member of that project.
* If only an org_id is supplied the user must belong to the organisation.
* Results are automatically truncated to protect large exports (per_page <= 50
  for interactive queries, export_csv limited to 100 rows).
"""QUILT (Quality Integrate Learning Tool) API endpoints.

The API exposes two endpoints:

```
POST /api/quilt/query/
GET  /api/quilt/sources/
```

Requests must be made by authenticated users. All responses are scoped to the
organisation/project the user is allowed to access. A ``project_id`` or
``org_id`` must be provided in the query payload, and membership is validated
before any data is returned. Interactive queries are capped at 50 rows per page
(default 25). Export requests are limited to 100 rows and return a flag when the
limit is reached. The service intentionally avoids returning raw file contents;
instead it emits application URLs that the caller can follow.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Max, Q
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_POST

from organizations.models import Membership, Organization
from projects.models import Project, ProjectMember

from ..models import MaterialHeatDraft, Weld, WeldRepair

DEFAULT_PER_PAGE = 25
MAX_PER_PAGE = 50
MAX_EXPORT_ROWS = 100
DEFAULT_TOP_N = 5


@dataclass
class QueryScope:
    org: Organization
    project: Project | None = None


class JsonError(Exception):
    """Raised when the request payload is invalid."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _load_json(request: HttpRequest) -> dict[str, Any]:
    body = request.body.decode("utf-8") or "{}"
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise JsonError(f"Invalid JSON payload: {exc}")


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    parsed = parse_date(str(value))
    return parsed


def _ensure_org_membership(user, org: Organization) -> None:
    if not Membership.objects.filter(org=org, user=user).exists():
        raise PermissionDenied("User is not a member of this organisation")


def _ensure_project_membership(user, project: Project) -> None:
    if not ProjectMember.objects.filter(project=project, user=user).exists():
        raise PermissionDenied("User is not a member of this project")


def _resolve_scope(request: HttpRequest, payload: dict[str, Any]) -> QueryScope:
    org_id = payload.get("org_id")
    project_id = payload.get("project_id")

    project: Project | None = None
    org: Organization | None = None

    if project_id:
        project = get_object_or_404(Project, id=project_id)
        _ensure_project_membership(request.user, project)
        org = project.org
    if org_id:
        org = get_object_or_404(Organization, id=org_id)
        _ensure_org_membership(request.user, org)
        if project and project.org_id != org.id:
            raise JsonError("Project does not belong to the supplied organisation", status=400)
    if not org:
        raise JsonError("An org_id or project_id is required")
    return QueryScope(org=org, project=project)


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


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        value_int = int(value)
        if value_int <= 0:
            return default
        return value_int
    except (TypeError, ValueError):
        return default


def _build_repair_filter(start: date | None, end: date | None) -> Q | None:
    filters: list[Q] = []
    if start:
        filters.append(Q(repairs__repair_date__gte=start) | Q(repairs__flagged_at__gte=start))
    if end:
        filters.append(Q(repairs__repair_date__lte=end) | Q(repairs__flagged_at__lte=end))
    if not filters:
        return None
    q = filters[0]
    for clause in filters[1:]:
        q &= clause
    return q


def _build_weld_filters(payload_filters: dict[str, Any] | None) -> dict[str, Any]:
    payload_filters = payload_filters or {}
    filters: dict[str, Any] = {}
    start = _parse_date(payload_filters.get("start_date"))
    end = _parse_date(payload_filters.get("end_date"))
    if start:
        filters["start_date"] = start
    if end:
        filters["end_date"] = end
    if payload_filters.get("welder_id"):
        filters["welder_id"] = payload_filters["welder_id"]
    if payload_filters.get("wps"):
        filters["wps"] = str(payload_filters["wps"])
    return filters


def _apply_weld_queryset_filters(qs, scope: QueryScope, filters: dict[str, Any]):
    qs = qs.filter(project__org=scope.org)
    if scope.project:
        qs = qs.filter(project=scope.project)
    if filters.get("start_date"):
        qs = qs.filter(Q(weld_date__gte=filters["start_date"]) | Q(date_welded__gte=filters["start_date"]))
    if filters.get("end_date"):
        qs = qs.filter(Q(weld_date__lte=filters["end_date"]) | Q(date_welded__lte=filters["end_date"]))
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


def _apply_repair_queryset_filters(qs, scope: QueryScope, filters: dict[str, Any]):
    qs = qs.filter(weld__project__org=scope.org)
    if scope.project:
        qs = qs.filter(weld__project=scope.project)
    if filters.get("start_date"):
        start = filters["start_date"]
        qs = qs.filter(Q(repair_date__gte=start) | Q(flagged_at__gte=start))
    if filters.get("end_date"):
        end = filters["end_date"]
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


def _build_scope_text(scope: QueryScope) -> str:
    if scope.project:
        return scope.project.name
    return scope.org.name


def _format_percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _build_answer(scope: QueryScope, filters: dict[str, Any], total_welds: int, repaired_welds: int, repair_rate: float, top_welders: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    period = ""
    start = filters.get("start_date")
    end = filters.get("end_date")
    if start and end:
        period = f" ({start:%b %d, %Y} – {end:%b %d, %Y})"
    elif start:
        period = f" (since {start:%b %d, %Y})"
    elif end:
        period = f" (through {end:%b %d, %Y})"

    scope_text = _build_scope_text(scope)
    if total_welds == 0:
        return f"No weld production recorded for {scope_text}{period}."

    answer = [
        f"In {scope_text}{period}: {total_welds} total welds, {repaired_welds} repaired",
        f"→ repair rate {_format_percent(repair_rate)}."
    ]
    if top_welders:
        welder_lines = ", ".join(
            f"{welder['name']} ({welder['repairs']} repairs)" for welder in top_welders
        )
        answer.append(f"Top {len(top_welders)} welders: {welder_lines}.")
    if meta.get("total", 0) > meta.get("returned", 0):
        answer.append(
            f"Showing {meta['returned']} of {meta['total']}. Use 'Show more' for the next page."
        )
    return " ".join(answer)


def _material_sources_for_weld(weld: Weld) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    org_slug = weld.project.org.slug
    project_slug = weld.project.slug
    try:
        weld_url = reverse(
            "welds:weld_log",
            kwargs={"org_slug": org_slug, "project_slug": project_slug},
        ) + f"?weld={weld.id}"
    except Exception:  # pragma: no cover - defensive fallback
        weld_url = f"/o/{org_slug}/projects/{project_slug}/welds/{weld.id}/"
    sources.append({"type": "weld", "id": weld.id, "url": weld_url})

    for heat_attr in ("material1_heat", "material2_heat"):
        heat = getattr(weld, heat_attr, None)
        if not heat:
            continue
        mtr = getattr(heat, "mtr_document", None)
        if not mtr:
            continue
        parser_confidence = None
        draft = (
            MaterialHeatDraft.objects.filter(file_node=mtr)
            .order_by("-parsed_at")
            .only("confidence")
            .first()
        )
        if draft and draft.confidence is not None:
            parser_confidence = float(draft.confidence)
        try:
            file_url = reverse(
                "drive_file",
                kwargs={
                    "org_slug": org_slug,
                    "project_slug": project_slug,
                    "file_id": mtr.id,
                },
            )
        except Exception:  # pragma: no cover - fallback for environments without drive urls
            file_url = f"/o/{org_slug}/p/{project_slug}/drive/file/{mtr.id}/"
        source_payload = {"type": "file", "id": mtr.id, "url": file_url}
        if parser_confidence is not None:
            source_payload["parser_confidence"] = parser_confidence
        sources.append(source_payload)
    return sources


def _serialise_weld(weld: Weld) -> dict[str, Any]:
    last_repair_date = weld.last_repair_date or weld.last_flagged_date
    welder = weld.primary_welder
    welder_payload = None
    if welder:
        profile_url = f"/o/{weld.project.org.slug}/welders/{welder.id}/"
        welder_payload = {"id": welder.id, "name": welder.name, "profile_url": profile_url}
    sources = _material_sources_for_weld(weld)
    weld_url = next((source["url"] for source in sources if source.get("type") == "weld"), None)
    return {
        "id": weld.id,
        "identifier": weld.weld_id,
        "status": weld.disposition,
        "repairs_count": weld.repairs_count,
        "last_repair_date": last_repair_date.isoformat() if last_repair_date else None,
        "welder": welder_payload,
        "weld_url": weld_url,
        "sources": sources,
    }


def _top_welders_payload(rows: Iterable[dict[str, Any]], org_slug: str) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for row in rows:
        welder_id = row.get("weld__primary_welder_id")
        name = row.get("weld__primary_welder__name")
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


@login_required
@require_POST
def quilt_query(request: HttpRequest) -> JsonResponse:
    try:
        payload = _load_json(request)
        scope = _resolve_scope(request, payload)
    except JsonError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status)
    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc)}, status=403)

    filters = _build_weld_filters(payload.get("filters"))

    per_page_requested = _coerce_positive_int(payload.get("per_page"), DEFAULT_PER_PAGE)
    intent = _infer_intent(payload.get("query"), payload.get("intent"))
    if intent == "export_csv":
        per_page = min(per_page_requested or MAX_EXPORT_ROWS, MAX_EXPORT_ROWS)
    else:
        per_page = min(per_page_requested, MAX_PER_PAGE)
    page = _coerce_positive_int(payload.get("page"), 1)
    top_n = _coerce_positive_int(payload.get("top_n"), DEFAULT_TOP_N)

    welds_qs = Weld.objects.select_related(
        "project",
        "project__org",
        "primary_welder",
        "material1_heat__mtr_document",
        "material2_heat__mtr_document",
    )
    welds_qs = _apply_weld_queryset_filters(welds_qs, scope, filters)

    repairs_filter = _build_repair_filter(filters.get("start_date"), filters.get("end_date"))
    welds_qs = welds_qs.filter(repairs__isnull=False)
    if repairs_filter is not None:
        welds_qs = welds_qs.filter(repairs_filter)
    welds_qs = welds_qs.annotate(
        repairs_count=Count("repairs", filter=repairs_filter, distinct=True),
        last_repair_date=Max("repairs__repair_date"),
        last_flagged_date=Max("repairs__flagged_at"),
    ).order_by("-last_repair_date", "-last_flagged_date", "-id").distinct()

    total_matches = welds_qs.count()
    offset = (page - 1) * per_page
    page_qs = welds_qs[offset : offset + per_page]
    weld_items = [_serialise_weld(weld) for weld in page_qs]

    repairs_qs = WeldRepair.objects.select_related("weld", "weld__project", "weld__project__org")
    repairs_qs = _apply_repair_queryset_filters(repairs_qs, scope, filters)

    repaired_weld_count = repairs_qs.values("weld_id").distinct().count()

    total_welds_qs = Weld.objects.filter(project__org=scope.org)
    if scope.project:
        total_welds_qs = total_welds_qs.filter(project=scope.project)
    if filters.get("start_date"):
        total_welds_qs = total_welds_qs.filter(
            Q(weld_date__gte=filters["start_date"]) | Q(date_welded__gte=filters["start_date"])
        )
    if filters.get("end_date"):
        total_welds_qs = total_welds_qs.filter(
            Q(weld_date__lte=filters["end_date"]) | Q(date_welded__lte=filters["end_date"])
        )

    total_welds = total_welds_qs.count()
    repair_rate = (repaired_weld_count / total_welds) if total_welds else 0

    top_rows = (
        repairs_qs.values("weld__primary_welder_id", "weld__primary_welder__name")
        .annotate(repairs=Count("id"))
        .order_by("-repairs", "weld__primary_welder__name")[:top_n]
    )
    top_welders = _top_welders_payload(top_rows, scope.org.slug)

    meta = {
        "page": page,
        "per_page": per_page,
        "returned": len(weld_items),
        "total": total_matches,
    }
    if intent == "export_csv" and total_matches > MAX_EXPORT_ROWS:
        meta["export_limited"] = True

    answer = _build_answer(scope, filters, total_welds, repaired_weld_count, repair_rate, top_welders, meta)

    sources = []
    seen = set()
    for weld in weld_items:
        for source in weld.get("sources", []):
            key = (source.get("type"), source.get("id"))
            if key in seen:
                continue
            seen.add(key)
            sources.append(source)

    response = {
        "answer": answer,
        "kpi": {
            "repair_rate": repair_rate,
            "total_welds": total_welds,
            "repaired_welds": repaired_weld_count,
            "top_welders": top_welders,
        },
        "welds": weld_items,
        "meta": meta,
        "sources": sources,
    }

    if not weld_items and repaired_weld_count == 0:
        response["answer"] = (
            "No repaired welds found for that range. Try refining the filters or widening the dates."
        )

    if intent == "export_csv" and meta.get("export_limited"):
        response["answer"] += " Export limited to 100 rows. For larger exports use the background export flow."

    return JsonResponse(response)


@login_required
@require_GET
def quilt_sources(request: HttpRequest) -> JsonResponse:
    memberships = Membership.objects.filter(user=request.user).select_related("org")
    projects = (
        ProjectMember.objects.filter(user=request.user)
        .select_related("project", "project__org")
        .order_by("project__org__name", "project__name")
    )

    project_map: dict[int, list[dict[str, Any]]] = {}
    for pm in projects:
        org_id = pm.project.org_id
        project_map.setdefault(org_id, []).append(
            {
                "id": pm.project.id,
                "name": pm.project.name,
                "slug": pm.project.slug,
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

    return JsonResponse({"organizations": org_payload})
