import csv
import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import connection, transaction, IntegrityError
from django.db.models import Q
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden, HttpResponse
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from django.middleware.csrf import get_token

from organizations.decorators import require_membership
from projects.models import Project, ProjectMember
from projects.utils import (
    assert_project_not_archived,
    get_project_for_request,
    user_has_project_access,
)

from drive.models import FileNode

from .analytics import build_dashboard_analytics, build_drilldown
from .models import (
    MaterialHeat,
    NDERig,
    RepairAttempt,
    Reinspection,
    Weld,
    WeldEvent,
    WeldHistory,
    WeldInspection,
    WeldRepair,
    Welder,
)
from .services import close_repairs_for_weld, mark_repair_for_weld


User = get_user_model()


logger = logging.getLogger(__name__)


def _existing_table_names():
    return set(connection.introspection.table_names())


def _missing_weld_tables():
    existing = _existing_table_names()
    required = {
        MaterialHeat._meta.db_table: "material heat records",
        NDERig._meta.db_table: "NDE rigs",
        Welder._meta.db_table: "welders",
        Weld._meta.db_table: "weld log entries",
        WeldHistory._meta.db_table: "weld history entries",
        WeldEvent._meta.db_table: "weld events",
    }
    missing = [label for table, label in required.items() if table not in existing]
    if missing:
        logger.warning("Missing weld tables detected", extra={"missing": missing})
    return missing


def _migrations_required_message(missing_labels):
    if not missing_labels:
        return ""
    missing = ", ".join(missing_labels)
    return (
        "The weld tracking tables are not available in this environment yet "
        f"(missing: {missing}). Run `python manage.py migrate` and reload this page."
    )


def _require_project_membership(request, project):
    if not user_has_project_access(request.user, project):
        return HttpResponseForbidden("No project access")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        try:
            assert_project_not_archived(project)
        except PermissionDenied:
            return HttpResponseForbidden("This project is archived and locked.")
    return None


def _user_can_rollback(user, project) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    membership = ProjectMember.objects.filter(project=project, user=user).first()
    if not membership:
        return False
    return membership.role in ProjectMember.Role.managerial_roles()


def _decimal_to_str(value):
    if value is None:
        return ""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _serialize_heat(
    prefix: str,
    weld: Weld,
    *,
    org_slug: str | None = None,
    project_slug: str | None = None,
) -> dict:
    heat = getattr(weld, f"{prefix}_heat")
    mtr = getattr(heat, "mtr_document", None)
    heat_payload = {}
    if heat:
        heat_payload = _material_heat_payload(
            heat, org_slug=org_slug, project_slug=project_slug
        )
    return {
        f"{prefix}_heat_id": heat.id if heat else None,
        f"{prefix}_heat_number": heat.heat_number if heat else "",
        f"{prefix}_description": getattr(weld, f"{prefix}_description"),
        f"{prefix}_grade": getattr(weld, f"{prefix}_grade"),
        f"{prefix}_outer_diameter_in": _decimal_to_str(
            getattr(weld, f"{prefix}_outer_diameter_in")
        ),
        f"{prefix}_wall_thickness_in": _decimal_to_str(
            getattr(weld, f"{prefix}_wall_thickness_in")
        ),
        f"{prefix}_wps_number": heat.wps_number if heat else "",
        f"{prefix}_mtr_document_id": heat_payload.get("mtr_document_id"),
        f"{prefix}_mtr_document_name": heat_payload.get("mtr_document_name", ""),
        f"{prefix}_mtr_document_url": heat_payload.get("mtr_document_url", ""),
    }


def _format_user_display(user) -> str:
    if not user:
        return ""
    full_name = getattr(user, "get_full_name", None)
    if callable(full_name):
        name = full_name()
        if name:
            return name
    for attr in ("name", "email", "username"):
        value = getattr(user, attr, "")
        if value:
            return value
    return str(user)


def _choice_options(choices) -> list[dict]:
    return [{"value": value, "label": label} for value, label in choices]


def _folder_url(folder, *, org_slug: str | None = None, project_slug: str | None = None) -> str:
    if not folder or not org_slug or not project_slug:
        return ""
    return reverse(
        "drive_folder",
        kwargs={
            "org_slug": org_slug,
            "project_slug": project_slug,
            "folder_id": folder.id,
        },
    )


def _serialize_weld(
    weld: Weld,
    *,
    org_slug: str | None = None,
    project_slug: str | None = None,
) -> dict:
    payload = {
        "id": weld.id,
        "weld_id": weld.weld_id,
        "nde_number": weld.nde_number,
        "nde_type": weld.nde_type,
        "nde_type_label": weld.get_nde_type_display() if weld.nde_type else "",
        "drawing_number": weld.drawing_number,
        "weld_type": weld.weld_type or None,
        "weld_type_label": weld.get_weld_type_display() if weld.weld_type else "",
        "date_welded": weld.date_welded.isoformat() if weld.date_welded else "",
        "welder_stencil_root_hotpass": weld.welder_stencil_root_hotpass,
        "welder_stencil_fill": weld.welder_stencil_fill,
        "welder_stencil_cap": weld.welder_stencil_cap,
        "welder_stencil_repair": weld.welder_stencil_repair,
        "nde_date": weld.nde_date.isoformat() if weld.nde_date else "",
        "nde_rig_id": weld.nde_rig_id,
        "nde_rig_name": weld.nde_rig.name if weld.nde_rig else "",
        "nde_rig_folder_url": _folder_url(
            getattr(weld.nde_rig, "qualification_folder", None),
            org_slug=org_slug,
            project_slug=project_slug,
        ),
        "nde_rig_folder_name": (
            weld.nde_rig.qualification_folder.name
            if getattr(weld.nde_rig, "qualification_folder", None)
            else ""
        ),
        "repair_type": weld.repair_type,
        "repair_type_label": weld.get_repair_type_display()
        if weld.repair_type
        else "",
        "disposition": weld.disposition,
        "disposition_label": weld.get_disposition_display(),
        "disposition_comment": weld.disposition_comment,
        "created_at": weld.created_at.isoformat(),
        "updated_at": weld.updated_at.isoformat(),
        "created_by_id": weld.created_by_id,
        "created_by_name": _format_user_display(weld.created_by),
        "updated_by_id": weld.updated_by_id,
        "updated_by_name": _format_user_display(weld.updated_by),
    }
    payload.update(
        _serialize_heat(
            "material1",
            weld,
            org_slug=org_slug,
            project_slug=project_slug,
        )
    )
    payload.update(
        _serialize_heat(
            "material2",
            weld,
            org_slug=org_slug,
            project_slug=project_slug,
        )
    )
    return payload


def _calculate_changed_fields(before: dict | None, after: dict | None) -> list[str]:
    before = before or {}
    after = after or {}
    keys = set(before.keys()) | set(after.keys())
    return sorted(key for key in keys if before.get(key) != after.get(key))


def _build_event_changes(
    before: dict | None, after: dict | None, changed_fields: list[str]
) -> dict:
    before = before or {}
    after = after or {}
    payload: dict[str, list] = {}
    for field in changed_fields:
        payload[field] = [before.get(field), after.get(field)]
    return payload


def _extract_request_ip(request) -> str:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        ip = forwarded_for.split(",")[0].strip()
        if ip:
            return ip[:45]
    remote_addr = request.META.get("REMOTE_ADDR")
    if remote_addr:
        return str(remote_addr)[:45]
    return ""


def _extract_user_agent(request) -> str:
    user_agent = request.META.get("HTTP_USER_AGENT")
    if not user_agent:
        return ""
    return str(user_agent)[:512]


def _record_weld_event(
    *,
    request,
    weld: Weld,
    action: str,
    changes: dict,
):
    actor = None
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        actor = user
    try:
        WeldEvent.objects.create(
            weld=weld,
            action=action,
            actor=actor,
            changes=changes or {},
            ip_address=_extract_request_ip(request),
            user_agent=_extract_user_agent(request),
        )
    except Exception:
        logger.exception(
            "Failed to record weld event",
            extra={
                "weld_id": getattr(weld, "weld_id", None),
                "weld_pk": getattr(weld, "pk", None),
                "action": action,
            },
        )


def _parse_decimal_value(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _parse_date_value(value):
    if not value:
        return None
    if isinstance(value, str):
        return parse_date(value)
    return value


def _parse_int_value(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _snapshot_to_model_updates(snapshot: dict | None, *, weld: Weld | None = None) -> dict:
    snapshot = snapshot or {}

    def get_value(key, default=None):
        if key in snapshot:
            return snapshot.get(key)
        if weld is not None:
            return getattr(weld, key, default)
        return default

    updates: dict = {}

    weld_identifier = snapshot.get("weld_id")
    if weld_identifier:
        updates["weld_id"] = weld_identifier
    elif weld is not None:
        updates["weld_id"] = weld.weld_id

    updates["nde_number"] = get_value("nde_number", "") or ""
    updates["nde_type"] = get_value("nde_type") or None
    updates["drawing_number"] = get_value("drawing_number", "") or ""

    updates["material1_heat_id"] = _parse_int_value(
        get_value("material1_heat_id")
    )
    updates["material1_description"] = get_value("material1_description", "") or ""
    updates["material1_grade"] = get_value("material1_grade", "") or ""
    updates["material1_outer_diameter_in"] = _parse_decimal_value(
        get_value("material1_outer_diameter_in")
    )
    updates["material1_wall_thickness_in"] = _parse_decimal_value(
        get_value("material1_wall_thickness_in")
    )

    updates["material2_heat_id"] = _parse_int_value(
        get_value("material2_heat_id")
    )
    updates["material2_description"] = get_value("material2_description", "") or ""
    updates["material2_grade"] = get_value("material2_grade", "") or ""
    updates["material2_outer_diameter_in"] = _parse_decimal_value(
        get_value("material2_outer_diameter_in")
    )
    updates["material2_wall_thickness_in"] = _parse_decimal_value(
        get_value("material2_wall_thickness_in")
    )

    updates["weld_type"] = get_value("weld_type") or ""
    updates["date_welded"] = _parse_date_value(get_value("date_welded"))

    updates["welder_stencil_root_hotpass"] = (
        get_value("welder_stencil_root_hotpass", "") or ""
    )
    updates["welder_stencil_fill"] = get_value("welder_stencil_fill", "") or ""
    updates["welder_stencil_cap"] = get_value("welder_stencil_cap", "") or ""
    updates["welder_stencil_repair"] = get_value("welder_stencil_repair", "") or ""

    updates["nde_date"] = _parse_date_value(get_value("nde_date"))
    updates["nde_rig_id"] = _parse_int_value(get_value("nde_rig_id"))
    updates["repair_type"] = get_value("repair_type") or None
    updates["disposition"] = get_value("disposition", Weld.Disposition.PENDING)
    updates["disposition_comment"] = get_value("disposition_comment", "") or ""

    return updates


def _serialize_history_entry(entry: WeldHistory) -> dict:
    changed_by = entry.changed_by
    return {
        "id": entry.id,
        "weld_id": getattr(entry.weld, "weld_id", ""),
        "change_type": entry.change_type,
        "changed_fields": entry.changed_fields or [],
        "before": entry.before or {},
        "after": entry.after or {},
        "changed_by": {
            "id": getattr(changed_by, "id", None),
            "display_name": _format_user_display(changed_by) or "System",
        },
        "reason": entry.reason or "",
        "created_at": entry.created_at.isoformat(),
    }


def _material_heat_payload(
    heat: MaterialHeat,
    *,
    org_slug: str | None = None,
    project_slug: str | None = None,
) -> dict:
    mtr = heat.mtr_document
    if mtr:
        if org_slug and project_slug:
            mtr_url = reverse(
                "drive_file",
                kwargs={
                    "org_slug": org_slug,
                    "project_slug": project_slug,
                    "file_id": mtr.id,
                },
            )
        else:
            mtr_url = ""
        mtr_name = mtr.name
    else:
        mtr_url = ""
        mtr_name = ""
    return {
        "id": heat.id,
        "heat_number": heat.heat_number,
        "description": heat.description,
        "material_grade": heat.material_grade,
        "outer_diameter_in": _decimal_to_str(heat.outer_diameter_in),
        "wall_thickness_in": _decimal_to_str(heat.wall_thickness_in),
        "wps_number": heat.wps_number,
        "mtr_document_id": mtr.id if mtr else None,
        "mtr_document_name": mtr_name,
        "mtr_document_url": mtr_url,
    }


@require_membership("GUEST")
def weld_log(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        logger.warning(
            "User without access attempted to load weld log",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
            },
        )
        return forbidden

    missing_tables = _missing_weld_tables()
    setup_error = _migrations_required_message(missing_tables)

    logger.info(
        "Rendering weld log",
        extra={
            "user_id": getattr(request.user, "id", None),
            "org_slug": org_slug,
            "project_slug": project_slug,
            "setup_error": setup_error,
        },
    )

    context = {
        "org": request.org,
        "project": project,
        "setup_error": setup_error,
    }

    if not setup_error:
        context.update(
            {
                "data_url": reverse(
                    "welds:weld_log_data",
                    kwargs={
                        "org_slug": request.org.slug,
                        "project_slug": project.slug,
                    },
                ),
                "heat_options_url": reverse(
                    "welds:weld_material_heat_options",
                    kwargs={
                        "org_slug": request.org.slug,
                        "project_slug": project.slug,
                    },
                ),
                "heat_search_url": reverse(
                    "welds:weld_material_heat_search",
                    kwargs={
                        "org_slug": request.org.slug,
                        "project_slug": project.slug,
                    },
                ),
                "weld_history_page_url": reverse(
                    "welds:weld_history_page",
                    kwargs={
                        "org_slug": request.org.slug,
                        "project_slug": project.slug,
                    },
                ),
                "nde_rigs_url": reverse(
                    "welds:weld_nde_rig_options",
                    kwargs={
                        "org_slug": request.org.slug,
                        "project_slug": project.slug,
                    },
                ),
                "welder_options_url": reverse(
                    "welds:weld_welder_options",
                    kwargs={
                        "org_slug": request.org.slug,
                        "project_slug": project.slug,
                    },
                ),
                "disposition_options": _choice_options(Weld.Disposition.choices),
                "disposition_default": Weld.Disposition.PENDING,
                "weld_type_options": _choice_options(Weld.WeldType.choices),
                "repair_type_options": _choice_options(Weld.RepairType.choices),
                "nde_type_options": _choice_options(Weld.NDEType.choices),
                "weld_history_url_template": reverse(
                    "welds:weld_history",
                    kwargs={
                        "org_slug": request.org.slug,
                        "project_slug": project.slug,
                        "weld_pk": 0,
                    },
                ).replace("/0/", "/{weld_id}/"),
                "weld_history_rollback_url_template": reverse(
                    "welds:weld_history_rollback",
                    kwargs={
                        "org_slug": request.org.slug,
                        "project_slug": project.slug,
                        "history_id": 0,
                    },
                ).replace("/0/", "/{history_id}/"),
                "can_rollback_welds": _user_can_rollback(request.user, project),
            }
        )

    status = 503 if setup_error else 200
    return render(request, "welds/weld_log.html", context, status=status)


@require_membership("GUEST")
def repair_log(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        return forbidden

    project_repairs_url = reverse(
        "welds:project_repairs",
        kwargs={"org_slug": request.org.slug, "project_id": project.id},
    )
    repair_update_template = reverse(
        "welds:update_repair",
        kwargs={"org_slug": request.org.slug, "repair_id": 0},
    ).replace("/0/", "/{id}/")
    attempt_template = reverse(
        "welds:repair_add_attempt",
        kwargs={"org_slug": request.org.slug, "repair_id": 0},
    ).replace("/0/", "/{id}/")
    reinspection_template = reverse(
        "welds:repair_add_reinspection",
        kwargs={"org_slug": request.org.slug, "repair_id": 0},
    ).replace("/0/", "/{id}/")
    close_template = reverse(
        "welds:repair_close",
        kwargs={"org_slug": request.org.slug, "repair_id": 0},
    ).replace("/0/", "/{id}/")

    assigned_options = []
    memberships = (
        ProjectMember.objects.filter(project=project)
        .select_related("user")
        .order_by("user__username")
    )
    for membership in memberships:
        if not membership.user_id:
            continue
        assigned_options.append(
            {
                "value": membership.user_id,
                "label": _format_user_display(membership.user),
            }
        )

    config = {
        "projectRepairsUrl": project_repairs_url,
        "repairUpdateUrlTemplate": repair_update_template,
        "attemptCreateUrlTemplate": attempt_template,
        "reinspectionCreateUrlTemplate": reinspection_template,
        "repairCloseUrlTemplate": close_template,
        "assignedOptions": assigned_options,
        "csrfToken": get_token(request),
        "perPage": 25,
        "editableFields": [
            "repair_stencil",
            "repair_date",
            "nde_type",
            "comments",
            "assigned_to_id",
        ],
    }

    context = {
        "org": request.org,
        "project": project,
        "repair_log_config": config,
    }
    return render(request, "welds/repair_log.html", context)


@require_membership("GUEST")
@require_http_methods(["GET", "POST"])
def weld_log_data(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        logger.warning(
            "User without access attempted to query weld log data",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
            },
        )
        return forbidden

    missing_tables = _missing_weld_tables()
    if missing_tables:
        message = _migrations_required_message(missing_tables)
        logger.error(
            "Weld log data unavailable due to missing tables",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "missing": missing_tables,
            },
        )
        return JsonResponse({"error": message}, status=503)

    if request.method == "GET":
        rows = [
            _serialize_weld(
                w,
                org_slug=request.org.slug,
                project_slug=project.slug,
            )
            for w in project.welds.select_related(
                "material1_heat",
                "material1_heat__mtr_document",
                "material2_heat",
                "material2_heat__mtr_document",
                "nde_rig",
                "nde_rig__qualification_folder",
            )
        ]
        logger.info(
            "Loaded weld log rows",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "row_count": len(rows),
            },
        )
        return JsonResponse({"rows": rows, "row_count": len(rows)})

    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except json.JSONDecodeError:
        logger.exception(
            "Failed to decode weld log payload",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
            },
        )
        return HttpResponseBadRequest("Invalid JSON body")

    weld_id = payload.get("weld_id", "").strip()
    if not weld_id:
        logger.warning(
            "Rejecting weld save without weld_id",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
            },
        )
        return JsonResponse({"error": "Weld ID is required."}, status=400)

    history_reason = str(payload.get("reason", "") or "").strip()

    def _clean_text(key):
        return str(payload.get(key, "") or "").strip()

    def _parse_decimal_field(key):
        value = payload.get(key)
        if value in (None, ""):
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError):
            raise ValueError(key)

    def _parse_date_field(key):
        value = payload.get(key)
        if not value:
            return None
        parsed = parse_date(str(value))
        if not parsed:
            raise ValueError(key)
        return parsed

    try:
        material1_outer_diameter = _parse_decimal_field(
            "material1_outer_diameter_in"
        )
        material1_wall_thickness = _parse_decimal_field(
            "material1_wall_thickness_in"
        )
        material2_outer_diameter = _parse_decimal_field(
            "material2_outer_diameter_in"
        )
        material2_wall_thickness = _parse_decimal_field(
            "material2_wall_thickness_in"
        )
    except ValueError as exc:
        logger.warning(
            "Rejecting weld save with invalid decimal",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "field": exc.args[0],
            },
        )
        return JsonResponse(
            {"error": f"Invalid numeric value for {exc.args[0].replace('_', ' ')}."},
            status=400,
        )

    try:
        date_welded = _parse_date_field("date_welded")
        nde_date = _parse_date_field("nde_date")
    except ValueError as exc:
        logger.warning(
            "Rejecting weld save with invalid date",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "field": exc.args[0],
            },
        )
        return JsonResponse(
            {"error": f"Invalid date value for {exc.args[0].replace('_', ' ')}."},
            status=400,
        )

    material1_heat_id = payload.get("material1_heat_id")
    material1_heat = None
    if material1_heat_id:
        try:
            material1_heat = MaterialHeat.objects.get(
                pk=material1_heat_id, org=request.org, is_active=True
            )
        except MaterialHeat.DoesNotExist:
            logger.warning(
                "Rejecting weld save with invalid material1 heat",
                extra={
                    "user_id": getattr(request.user, "id", None),
                    "org_slug": org_slug,
                    "project_slug": project_slug,
                    "heat_id": material1_heat_id,
                },
            )
            return JsonResponse(
                {"error": "Invalid Material 1 heat selection."}, status=400
            )

    material2_heat_id = payload.get("material2_heat_id")
    material2_heat = None
    if material2_heat_id:
        try:
            material2_heat = MaterialHeat.objects.get(
                pk=material2_heat_id, org=request.org, is_active=True
            )
        except MaterialHeat.DoesNotExist:
            logger.warning(
                "Rejecting weld save with invalid material2 heat",
                extra={
                    "user_id": getattr(request.user, "id", None),
                    "org_slug": org_slug,
                    "project_slug": project_slug,
                    "heat_id": material2_heat_id,
                },
            )
            return JsonResponse(
                {"error": "Invalid Material 2 heat selection."}, status=400
            )

    nde_rig_id = payload.get("nde_rig_id")
    nde_rig = None
    if nde_rig_id:
        try:
            nde_rig = NDERig.objects.get(
                pk=nde_rig_id,
                org=request.org,
                is_active=True,
            )
        except NDERig.DoesNotExist:
            logger.warning(
                "Rejecting weld save with invalid NDE rig",
                extra={
                    "user_id": getattr(request.user, "id", None),
                    "org_slug": org_slug,
                    "project_slug": project_slug,
                    "nde_rig_id": nde_rig_id,
                },
            )
            return JsonResponse({"error": "Invalid NDE rig."}, status=400)
        if nde_rig.project_id and nde_rig.project_id != project.id:
            logger.warning(
                "Rejecting weld save due to NDE rig project mismatch",
                extra={
                    "user_id": getattr(request.user, "id", None),
                    "org_slug": org_slug,
                    "project_slug": project_slug,
                    "nde_rig_id": nde_rig_id,
                },
            )
            return JsonResponse({"error": "Invalid NDE rig for this project."}, status=400)

    raw_weld_type = payload.get("weld_type")
    if raw_weld_type in (None, ""):
        weld_type_value = ""
    else:
        weld_type_value = str(raw_weld_type).strip()
        if weld_type_value not in Weld.WeldType.values:
            logger.warning(
                "Rejecting weld save with invalid weld type",
                extra={
                    "user_id": getattr(request.user, "id", None),
                    "org_slug": org_slug,
                    "project_slug": project_slug,
                    "weld_type": weld_type_value,
                },
            )
            return JsonResponse({"error": "Invalid weld type selection."}, status=400)

    raw_repair_type = payload.get("repair_type")
    if raw_repair_type in (None, ""):
        repair_type_value = None
    else:
        repair_type_value = str(raw_repair_type).strip()
        if repair_type_value not in Weld.RepairType.values:
            logger.warning(
                "Rejecting weld save with invalid repair type",
                extra={
                    "user_id": getattr(request.user, "id", None),
                    "org_slug": org_slug,
                    "project_slug": project_slug,
                    "repair_type": repair_type_value,
                },
            )
            return JsonResponse({"error": "Invalid repair type selection."}, status=400)

    raw_nde_type = payload.get("nde_type")
    if raw_nde_type in (None, ""):
        nde_type_value = None
    else:
        nde_type_value = str(raw_nde_type).strip()
        if nde_type_value not in Weld.NDEType.values:
            logger.warning(
                "Rejecting weld save with invalid NDE type",
                extra={
                    "user_id": getattr(request.user, "id", None),
                    "org_slug": org_slug,
                    "project_slug": project_slug,
                    "nde_type": nde_type_value,
                },
            )
            return JsonResponse({"error": "Invalid NDE type selection."}, status=400)

    disposition_raw = payload.get("disposition")
    disposition = (
        str(disposition_raw).strip()
        if disposition_raw not in (None, "")
        else Weld.Disposition.PENDING
    )
    if disposition not in Weld.Disposition.values:
        logger.warning(
            "Rejecting weld save with invalid disposition",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "disposition": disposition,
            },
        )
        return JsonResponse({"error": "Invalid weld disposition."}, status=400)

    disposition_comment = _clean_text("disposition_comment")
    if disposition in {Weld.Disposition.REPAIR, Weld.Disposition.CUT_OUT} and not disposition_comment:
        logger.warning(
            "Rejecting weld save missing disposition comment",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "disposition": disposition,
            },
        )
        return JsonResponse(
            {"error": "Comments are required when the weld is marked for repair or cut out."},
            status=400,
        )

    attrs = {
        "nde_number": _clean_text("nde_number"),
        "drawing_number": _clean_text("drawing_number"),
        "material1_heat": material1_heat,
        "material1_description": _clean_text("material1_description")
        or (material1_heat.description if material1_heat else ""),
        "material1_grade": _clean_text("material1_grade")
        or (material1_heat.material_grade if material1_heat else ""),
        "material1_outer_diameter_in": material1_outer_diameter
        if material1_outer_diameter is not None
        else material1_heat.outer_diameter_in if material1_heat else None,
        "material1_wall_thickness_in": material1_wall_thickness
        if material1_wall_thickness is not None
        else material1_heat.wall_thickness_in if material1_heat else None,
        "material2_heat": material2_heat,
        "material2_description": _clean_text("material2_description")
        or (material2_heat.description if material2_heat else ""),
        "material2_grade": _clean_text("material2_grade")
        or (material2_heat.material_grade if material2_heat else ""),
        "material2_outer_diameter_in": material2_outer_diameter
        if material2_outer_diameter is not None
        else material2_heat.outer_diameter_in if material2_heat else None,
        "material2_wall_thickness_in": material2_wall_thickness
        if material2_wall_thickness is not None
        else material2_heat.wall_thickness_in if material2_heat else None,
        "weld_type": weld_type_value,
        "date_welded": date_welded,
        "welder_stencil_root_hotpass": _clean_text("welder_stencil_root_hotpass"),
        "welder_stencil_fill": _clean_text("welder_stencil_fill"),
        "welder_stencil_cap": _clean_text("welder_stencil_cap"),
        "welder_stencil_repair": _clean_text("welder_stencil_repair"),
        "nde_date": nde_date,
        "nde_rig": nde_rig,
        "nde_type": nde_type_value,
        "repair_type": repair_type_value,
        "disposition": disposition,
        "disposition_comment": disposition_comment,
    }

    existing_qs = project.welds.filter(weld_id__iexact=weld_id)

    weld_pk = payload.get("id")
    if weld_pk:
        weld = get_object_or_404(Weld, pk=weld_pk, project=project)
        if existing_qs.exclude(pk=weld.pk).exists():
            logger.warning(
                "Rejecting weld update due to duplicate weld_id",
                extra={
                    "user_id": getattr(request.user, "id", None),
                    "org_slug": org_slug,
                    "project_slug": project_slug,
                    "weld_id": weld_id,
                    "weld_pk": weld_pk,
                },
            )
            return JsonResponse({"error": "Weld ID already exists for this project."}, status=400)
        before_snapshot = _serialize_weld(
            weld, org_slug=request.org.slug, project_slug=project.slug
        )
        with transaction.atomic():
            weld.weld_id = weld_id
            for field, value in attrs.items():
                setattr(weld, field, value)
            weld.updated_by = request.user
            weld.save()
            after_snapshot = _serialize_weld(
                weld, org_slug=request.org.slug, project_slug=project.slug
            )
            changed_fields = _calculate_changed_fields(
                before_snapshot, after_snapshot
            )
            WeldHistory.objects.create(
                weld=weld,
                changed_by=request.user,
                change_type=WeldHistory.ChangeType.UPDATE,
                changed_fields=changed_fields,
                before=before_snapshot,
                after=after_snapshot,
                reason=history_reason,
            )
        event_changes = _build_event_changes(
            before_snapshot, after_snapshot, changed_fields
        )
        _record_weld_event(
            request=request,
            weld=weld,
            action=WeldEvent.Action.UPDATE,
            changes=event_changes,
        )
        logger.info(
            "Updated weld entry",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "weld_id": weld_id,
                "weld_pk": weld.pk,
            },
        )
        return JsonResponse({"weld": after_snapshot})

    if existing_qs.exists():
        logger.warning(
            "Rejecting weld create due to duplicate weld_id",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "weld_id": weld_id,
            },
        )
        return JsonResponse({"error": "Weld ID already exists for this project."}, status=400)

    with transaction.atomic():
        weld = Weld.objects.create(
            project=project,
            weld_id=weld_id,
            created_by=request.user,
            updated_by=request.user,
            **attrs,
        )
        after_snapshot = _serialize_weld(
            weld, org_slug=request.org.slug, project_slug=project.slug
        )
        WeldHistory.objects.create(
            weld=weld,
            changed_by=request.user,
            change_type=WeldHistory.ChangeType.CREATE,
            changed_fields=sorted(after_snapshot.keys()),
            before={},
            after=after_snapshot,
            reason=history_reason,
        )
    event_changes = _build_event_changes(
        {}, after_snapshot, sorted(after_snapshot.keys())
    )
    _record_weld_event(
        request=request,
        weld=weld,
        action=WeldEvent.Action.CREATE,
        changes=event_changes,
    )
    logger.info(
        "Created weld entry",
        extra={
            "user_id": getattr(request.user, "id", None),
            "org_slug": org_slug,
            "project_slug": project_slug,
            "weld_id": weld_id,
            "weld_pk": weld.pk,
        },
    )
    return JsonResponse({"weld": after_snapshot}, status=201)


def _get_weld_for_history(project, weld_identifier):
    if not weld_identifier:
        return None
    return (
        project.welds.select_related(
            "material1_heat",
            "material1_heat__mtr_document",
            "material2_heat",
            "material2_heat__mtr_document",
            "nde_rig",
            "nde_rig__qualification_folder",
            "created_by",
            "updated_by",
        )
        .filter(weld_id__iexact=weld_identifier)
        .first()
    )


@require_membership("GUEST")
@require_http_methods(["GET"])
def weld_history_data(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        logger.warning(
            "User without access attempted to load weld history data",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
            },
        )
        return forbidden

    missing_tables = _missing_weld_tables()
    if missing_tables:
        message = _migrations_required_message(missing_tables)
        logger.error(
            "Weld history data unavailable due to missing tables",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "missing": missing_tables,
            },
        )
        return JsonResponse({"error": message}, status=503)

    weld_identifier = str(request.GET.get("weld_id", "")).strip()
    if not weld_identifier:
        return JsonResponse({"error": "Missing weld_id parameter."}, status=400)

    weld = _get_weld_for_history(project, weld_identifier)
    if not weld:
        logger.info(
            "Requested weld history for missing weld",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "weld_id": weld_identifier,
            },
        )
        return JsonResponse({"error": "Weld not found."}, status=404)

    weld_payload = _serialize_weld(
        weld, org_slug=request.org.slug, project_slug=project.slug
    )

    events_qs = weld.events.select_related("actor")
    events_payload = [
        {
            "at": event.created_at.isoformat(),
            "action": event.action,
            "actor_name": _format_user_display(event.actor) or None,
            "changes": event.changes or {},
        }
        for event in events_qs
    ]

    logger.info(
        "Loaded weld audit history",
        extra={
            "user_id": getattr(request.user, "id", None),
            "org_slug": org_slug,
            "project_slug": project_slug,
            "weld_id": weld_identifier,
            "event_count": len(events_payload),
        },
    )

    return JsonResponse({"weld": weld_payload, "events": events_payload})


@require_membership("GUEST")
@require_http_methods(["GET"])
def weld_history_page(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        logger.warning(
            "User without access attempted to load weld history page",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
            },
        )
        return forbidden

    weld_log_url = reverse(
        "welds:weld_log",
        kwargs={
            "org_slug": request.org.slug,
            "project_slug": project.slug,
        },
    )

    missing_tables = _missing_weld_tables()
    if missing_tables:
        message = _migrations_required_message(missing_tables)
        logger.error(
            "Weld history page unavailable due to missing tables",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "missing": missing_tables,
            },
        )
        return render(
            request,
            "welds/weld_history_detail.html",
            {
                "org": request.org,
                "project": project,
                "error": message,
                "weld": None,
                "events": [],
                "weld_log_url": weld_log_url,
            },
            status=503,
        )

    weld_identifier = str(request.GET.get("weld_id", "")).strip()
    if not weld_identifier:
        context = {
            "org": request.org,
            "project": project,
            "error": "Missing weld_id parameter.",
            "weld": None,
            "events": [],
            "weld_log_url": weld_log_url,
        }
        return render(
            request,
            "welds/weld_history_detail.html",
            context,
            status=400,
        )

    weld = _get_weld_for_history(project, weld_identifier)
    if not weld:
        context = {
            "org": request.org,
            "project": project,
            "error": "Weld not found.",
            "weld": None,
            "events": [],
            "weld_log_url": weld_log_url,
        }
        return render(
            request,
            "welds/weld_history_detail.html",
            context,
            status=404,
        )

    weld_payload = _serialize_weld(
        weld, org_slug=request.org.slug, project_slug=project.slug
    )
    events = [
        {
            "at": event.created_at,
            "action": event.action,
            "actor_name": _format_user_display(event.actor) or None,
            "changes": event.changes or {},
        }
        for event in weld.events.select_related("actor")
    ]

    logger.info(
        "Rendering weld history page",
        extra={
            "user_id": getattr(request.user, "id", None),
            "org_slug": org_slug,
            "project_slug": project_slug,
            "weld_id": weld_identifier,
            "event_count": len(events),
        },
    )

    context = {
        "org": request.org,
        "project": project,
        "weld": weld_payload,
        "events": events,
        "error": "",
        "weld_log_url": weld_log_url,
    }

    return render(request, "welds/weld_history_detail.html", context)


@require_membership("GUEST")
@require_http_methods(["GET"])
def weld_history_data(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        logger.warning(
            "User without access attempted to load weld history data",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
            },
        )
        return forbidden

    missing_tables = _missing_weld_tables()
    if missing_tables:
        message = _migrations_required_message(missing_tables)
        logger.error(
            "Weld history data unavailable due to missing tables",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "missing": missing_tables,
            },
        )
        return JsonResponse({"error": message}, status=503)

    weld_identifier = str(request.GET.get("weld_id", "")).strip()
    if not weld_identifier:
        return JsonResponse({"error": "Missing weld_id parameter."}, status=400)

    weld = (
        project.welds.select_related(
            "material1_heat",
            "material1_heat__mtr_document",
            "material2_heat",
            "material2_heat__mtr_document",
            "nde_rig",
            "nde_rig__qualification_folder",
            "created_by",
            "updated_by",
        )
        .filter(weld_id__iexact=weld_identifier)
        .first()
    )
    if not weld:
        logger.info(
            "Requested weld history for missing weld",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "weld_id": weld_identifier,
            },
        )
        return JsonResponse({"error": "Weld not found."}, status=404)

    weld_payload = _serialize_weld(
        weld, org_slug=request.org.slug, project_slug=project.slug
    )

    events_qs = weld.events.select_related("actor")
    events_payload = [
        {
            "at": event.created_at.isoformat(),
            "action": event.action,
            "actor_name": _format_user_display(event.actor) or None,
            "changes": event.changes or {},
        }
        for event in events_qs
    ]

    logger.info(
        "Loaded weld audit history",
        extra={
            "user_id": getattr(request.user, "id", None),
            "org_slug": org_slug,
            "project_slug": project_slug,
            "weld_id": weld_identifier,
            "event_count": len(events_payload),
        },
    )

    return JsonResponse({"weld": weld_payload, "events": events_payload})


@require_membership("GUEST")
@require_http_methods(["GET"])
def weld_history(request, org_slug, project_slug, weld_pk):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        logger.warning(
            "User without access attempted to load weld history",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "weld_pk": weld_pk,
            },
        )
        return forbidden

    weld = get_object_or_404(Weld, pk=weld_pk, project=project)
    page_number = request.GET.get("page", "1")
    per_page_raw = request.GET.get("per_page", "25")
    try:
        page = max(int(page_number), 1)
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(per_page_raw)
    except (TypeError, ValueError):
        per_page = 25
    per_page = max(1, min(per_page, 100))

    history_qs = (
        weld.history.select_related("changed_by", "weld")
        .order_by("-created_at", "-id")
    )
    paginator = Paginator(history_qs, per_page)
    page_obj = paginator.get_page(page)
    rows = [_serialize_history_entry(entry) for entry in page_obj]

    logger.info(
        "Loaded weld history",
        extra={
            "user_id": getattr(request.user, "id", None),
            "org_slug": org_slug,
            "project_slug": project_slug,
            "weld_pk": weld_pk,
            "page": page,
            "per_page": per_page,
            "row_count": len(rows),
        },
    )

    return JsonResponse(
        {
            "rows": rows,
            "page": page_obj.number,
            "per_page": per_page,
            "total": paginator.count,
        }
    )


@require_membership("GUEST")
@require_http_methods(["POST"])
def weld_history_rollback(request, org_slug, project_slug, history_id):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        logger.warning(
            "User without access attempted weld rollback",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "history_id": history_id,
            },
        )
        return forbidden

    if not _user_can_rollback(request.user, project):
        logger.warning(
            "Rollback forbidden due to role",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "history_id": history_id,
            },
        )
        return HttpResponseForbidden("Insufficient permissions for rollback")

    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except json.JSONDecodeError:
        logger.exception(
            "Failed to decode rollback payload",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "history_id": history_id,
            },
        )
        return HttpResponseBadRequest("Invalid JSON body")

    confirm = bool(payload.get("confirm"))
    rollback_reason = str(payload.get("reason", "") or "").strip()

    history_entry = get_object_or_404(
        WeldHistory.objects.select_related("weld", "changed_by"),
        pk=history_id,
        weld__project=project,
    )
    weld = history_entry.weld

    current_snapshot = _serialize_weld(
        weld, org_slug=request.org.slug, project_slug=project.slug
    )

    if current_snapshot != (history_entry.after or {}) and not confirm:
        logger.info(
            "Rollback conflict detected",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "history_id": history_id,
                "weld_pk": weld.pk,
            },
        )
        return JsonResponse(
            {
                "error": "conflict",
                "message": "Weld has changed since this history entry.",
                "current": current_snapshot,
                "target": history_entry.before or {},
                "requires_confirmation": True,
            },
            status=409,
        )

    if not history_entry.before:
        logger.warning(
            "Rollback requested to empty snapshot",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "history_id": history_id,
                "weld_pk": weld.pk,
            },
        )
        return JsonResponse(
            {"error": "Rollback to the initial creation state is not supported."},
            status=400,
        )

    updates = _snapshot_to_model_updates(history_entry.before, weld=weld)
    reason_text = (
        rollback_reason
        or f"Rollback to history entry {history_entry.id}"
    )

    with transaction.atomic():
        before_snapshot = current_snapshot
        for field, value in updates.items():
            setattr(weld, field, value)
        weld.updated_by = request.user
        try:
            weld.save()
        except IntegrityError:
            logger.exception(
                "Rollback failed due to integrity error",
                extra={
                    "user_id": getattr(request.user, "id", None),
                    "org_slug": org_slug,
                    "project_slug": project_slug,
                    "history_id": history_id,
                    "weld_pk": weld.pk,
                },
            )
            transaction.set_rollback(True)
            return JsonResponse(
                {
                    "error": "integrity_error",
                    "message": "Rollback failed due to a conflicting weld identifier.",
                },
                status=400,
            )
        after_snapshot = _serialize_weld(
            weld, org_slug=request.org.slug, project_slug=project.slug
        )
        changed_fields = _calculate_changed_fields(
            before_snapshot, after_snapshot
        )
        rollback_history = WeldHistory.objects.create(
            weld=weld,
            changed_by=request.user,
            change_type=WeldHistory.ChangeType.ROLLBACK,
            changed_fields=changed_fields,
            before=before_snapshot,
            after=after_snapshot,
            reason=reason_text,
        )

    logger.info(
        "Rolled back weld entry",
        extra={
            "user_id": getattr(request.user, "id", None),
            "org_slug": org_slug,
            "project_slug": project_slug,
            "history_id": history_id,
            "weld_pk": weld.pk,
        },
    )

    return JsonResponse(
        {
            "weld": after_snapshot,
            "history": _serialize_history_entry(rollback_history),
            "restored_from_history_id": history_entry.id,
        }
    )


@require_membership("GUEST")
def material_heat_options(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        logger.warning(
            "User without access attempted to query heat options",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
            },
        )
        return forbidden

    missing_tables = _missing_weld_tables()
    if missing_tables:
        message = _migrations_required_message(missing_tables)
        logger.error(
            "Heat options unavailable due to missing tables",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "missing": missing_tables,
            },
        )
        return JsonResponse({"error": message}, status=503)

    heats = (
        MaterialHeat.objects.filter(org=request.org, is_active=True)
        .select_related("mtr_document")
        .order_by("heat_number")
    )
    data = [
        _material_heat_payload(
            heat, org_slug=request.org.slug, project_slug=project.slug
        )
        for heat in heats
    ]
    logger.info(
        "Loaded heat options",
        extra={
            "user_id": getattr(request.user, "id", None),
            "org_slug": org_slug,
            "project_slug": project_slug,
            "heat_count": len(data),
        },
    )
    return JsonResponse({"heats": data})


@require_membership("GUEST")
def material_heat_search(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        logger.warning(
            "User without access attempted to search heat numbers",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
            },
        )
        return forbidden

    missing_tables = _missing_weld_tables()
    if missing_tables:
        message = _migrations_required_message(missing_tables)
        logger.error(
            "Heat search unavailable due to missing tables",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "missing": missing_tables,
            },
        )
        return JsonResponse({"error": message}, status=503)

    query = (request.GET.get("q", "") or "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})

    heats = (
        MaterialHeat.objects.filter(
            org=request.org, is_active=True, heat_number__icontains=query
        )
        .select_related("mtr_document")
        .order_by("heat_number")[:20]
    )

    results = []
    for heat in heats:
        payload = _material_heat_payload(
            heat, org_slug=request.org.slug, project_slug=project.slug
        )
        payload["grade"] = payload.get("material_grade", "")
        payload["od"] = payload.get("outer_diameter_in", "")
        payload["wall_thickness"] = payload.get("wall_thickness_in", "")
        results.append(payload)

    logger.info(
        "Performed heat search",
        extra={
            "user_id": getattr(request.user, "id", None),
            "org_slug": org_slug,
            "project_slug": project_slug,
            "query": query,
            "result_count": len(results),
        },
    )
    return JsonResponse({"results": results})


@require_membership("GUEST")
def welder_options(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        logger.warning(
            "User without access attempted to query welder options",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
            },
        )
        return forbidden

    missing_tables = _missing_weld_tables()
    if missing_tables:
        message = _migrations_required_message(missing_tables)
        logger.error(
            "Welder options unavailable due to missing tables",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "missing": missing_tables,
            },
        )
        return JsonResponse({"error": message}, status=503)

    welders = (
        Welder.objects.filter(org=request.org, is_active=True)
        .order_by("stencil", "name")
    )
    data = [
        {
            "id": welder.id,
            "name": welder.name,
            "stencil": welder.stencil,
        }
        for welder in welders
    ]
    logger.info(
        "Loaded welder options",
        extra={
            "user_id": getattr(request.user, "id", None),
            "org_slug": org_slug,
            "project_slug": project_slug,
            "welder_count": len(data),
        },
    )
    return JsonResponse({"welders": data})


@require_membership("GUEST")
def nde_rig_options(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        logger.warning(
            "User without access attempted to query NDE rigs",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
            },
        )
        return forbidden

    missing_tables = _missing_weld_tables()
    if missing_tables:
        message = _migrations_required_message(missing_tables)
        logger.error(
            "NDE rig options unavailable due to missing tables",
            extra={
                "user_id": getattr(request.user, "id", None),
                "org_slug": org_slug,
                "project_slug": project_slug,
                "missing": missing_tables,
            },
        )
        return JsonResponse({"error": message}, status=503)

    rigs = (
        NDERig.objects.filter(org=request.org, is_active=True)
        .filter(Q(project=project) | Q(project__isnull=True))
        .select_related("qualification_folder")
        .order_by("name")
    )
    data = [
        {
            "id": rig.id,
            "name": rig.name,
            "qualification_folder_url": _folder_url(
                rig.qualification_folder,
                org_slug=request.org.slug,
                project_slug=project.slug,
            ),
            "qualification_folder_name": rig.qualification_folder.name
            if rig.qualification_folder
            else "",
        }
        for rig in rigs
    ]
    logger.info(
        "Loaded NDE rig options",
        extra={
            "user_id": getattr(request.user, "id", None),
            "org_slug": org_slug,
            "project_slug": project_slug,
            "nde_rig_count": len(data),
        },
    )
    return JsonResponse({"nde_rigs": data})


def _parse_dashboard_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except (TypeError, ValueError):
        return None


def _parse_dashboard_filters(request):
    start_date = _parse_dashboard_date(request.GET.get("start_date"))
    end_date = _parse_dashboard_date(request.GET.get("end_date"))
    raw_welder_ids = request.GET.getlist("welder_ids") or request.GET.getlist(
        "welder_id"
    )
    welder_ids: list[int] = []
    for raw in raw_welder_ids:
        try:
            welder_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    filters = {
        "start_date": start_date,
        "end_date": end_date,
    }
    if welder_ids:
        filters["welder_ids"] = welder_ids
    stencil_id = request.GET.get("stencil_id")
    if stencil_id:
        filters["stencil_id"] = stencil_id
    heat_number = request.GET.get("heat_number")
    if heat_number:
        filters["heat_number"] = heat_number
    pipe_size = request.GET.get("pipe_size")
    if pipe_size:
        filters["pipe_size"] = pipe_size
    od_value = request.GET.get("od")
    if od_value:
        try:
            filters["od"] = Decimal(od_value)
        except (TypeError, InvalidOperation):
            pass
    wps_id = request.GET.get("wps_id")
    if wps_id:
        try:
            filters["wps_id"] = int(wps_id)
        except (TypeError, ValueError):
            pass
    return filters


def _jsonify(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonify(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_jsonify(item) for item in value]
    from .analytics import WeldLengthInfo  # local import to avoid circular

    if isinstance(value, WeldLengthInfo):
        return {
            "weld_id": value.weld_id,
            "length": float(value.length),
            "estimated": value.estimated,
            "source": value.source,
            "basis": value.basis,
        }
    return value


def _build_csv_response(dataset: str, analytics: dict) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f"attachment; filename=dashboard-{dataset}.csv"
    writer = csv.writer(response)
    if dataset == "daily_production":
        writer.writerow(["date", "weld_inches", "weld_count"])
        for entry in analytics.get("daily_production", []):
            writer.writerow(
                [
                    (entry["date"].isoformat() if entry["date"] else ""),
                    entry["weld_inches"],
                    entry["weld_count"],
                ]
            )
    elif dataset == "repair_rate":
        writer.writerow(["date", "repairs", "weld_inches", "rate_per_1000_inches"])
        for entry in analytics.get("repair_rate_series", []):
            writer.writerow(
                [
                    entry["date"].isoformat(),
                    entry["repairs"],
                    entry["weld_inches"],
                    entry["rate_per_1000_inches"],
                ]
            )
    else:
        writer.writerow(["date", "weld_inches"])
        for entry in analytics.get(dataset, []):
            writer.writerow(
                [
                    entry.get("date").isoformat() if entry.get("date") else "",
                    entry.get("weld_inches"),
                ]
            )
    return response


@require_membership("GUEST")
def weld_dashboard_analytics(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        return forbidden
    filters = _parse_dashboard_filters(request)
    analytics = build_dashboard_analytics(project, filters)
    dataset = request.GET.get("dataset", "daily_production")
    if request.GET.get("format") == "csv":
        return _build_csv_response(dataset, analytics)
    payload = {
        key: _jsonify(value)
        for key, value in analytics.items()
        if key != "length_info"
    }
    return JsonResponse(payload, safe=False)


@require_membership("GUEST")
def weld_dashboard_drilldown(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        return forbidden
    dimension = request.GET.get("dimension")
    key = request.GET.get("key")
    if not dimension or not key:
        return HttpResponseBadRequest("dimension and key are required")
    filters = _parse_dashboard_filters(request)
    rows = build_drilldown(project, filters, dimension, key)
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = "attachment; filename=repair-drilldown.csv"
        writer = csv.writer(response)
        writer.writerow(
            [
                "weld_id",
                "weld_date",
                "weld_length_inches",
                "length_estimated",
                "length_basis",
                "welder",
                "stencil",
                "heat_number",
                "pipe_size",
                "od",
                "wps",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["weld_id"],
                    row["weld_date"].isoformat() if row["weld_date"] else "",
                    row["weld_length_inches"],
                    row["length_estimated"],
                    row["length_basis"],
                    row["welder"],
                    row["stencil"],
                    row["heat_number"],
                    row["pipe_size"],
                    row["od"],
                    row["wps"],
                ]
        )
        return response
    return JsonResponse([_jsonify(row) for row in rows], safe=False)


def _serialize_attempt(attempt: RepairAttempt) -> dict:
    return {
        "id": attempt.id,
        "attempt_no": attempt.attempt_no,
        "performed_at": attempt.performed_at.isoformat()
        if attempt.performed_at
        else "",
        "wps_document_id": attempt.wps_document_id,
        "wps_document_name": getattr(attempt.wps_document, "name", ""),
        "welder_stencil": attempt.welder_stencil,
        "inches_repaired": _decimal_to_str(attempt.inches_repaired),
        "outcome": attempt.outcome,
        "notes": attempt.notes,
        "created_by_id": attempt.created_by_id,
        "created_by_name": _format_user_display(attempt.created_by),
        "created_at": attempt.created_at.isoformat() if attempt.created_at else "",
    }


def _serialize_reinspection(reinspection: Reinspection) -> dict:
    return {
        "id": reinspection.id,
        "reinspection_date": reinspection.reinspection_date.isoformat()
        if reinspection.reinspection_date
        else "",
        "reinspection_nderig": reinspection.reinspection_nderig,
        "reinspection_ndereport_ref": reinspection.reinspection_ndereport_ref,
        "reinspection_result": reinspection.reinspection_result,
        "inspector": reinspection.inspector,
        "notes": reinspection.notes,
        "created_by_id": reinspection.created_by_id,
        "created_by_name": _format_user_display(reinspection.created_by),
        "created_at": reinspection.created_at.isoformat() if reinspection.created_at else "",
    }


def _serialize_repair(repair: WeldRepair, *, include_children: bool = False) -> dict:
    weld = repair.weld
    payload = {
        "id": repair.id,
        "weld_id": weld.id,
        "weld_identifier": weld.weld_id,
        "project_id": weld.project_id,
        "repair_sequence": repair.repair_sequence,
        "status": repair.status,
        "flagged_at": repair.flagged_at.isoformat() if repair.flagged_at else "",
        "manual_flag": repair.manual_flag,
        "original_ndereport_ref": repair.original_ndereport_ref or "",
        "original_nderig": repair.original_nderig or "",
        "defect_code_snapshot": repair.defect_code_snapshot or "",
        "nde_type": repair.nde_type or "",
        "repair_date": repair.repair_date.isoformat() if repair.repair_date else "",
        "repair_stencil": repair.repair_stencil or "",
        "reinspection_nderig": repair.reinspection_nderig or "",
        "reinspection_result": repair.reinspection_result or "",
        "reinspection_passed_at": repair.reinspection_passed_at.isoformat()
        if repair.reinspection_passed_at
        else "",
        "last_reinspection_report_ref": repair.last_reinspection_report_ref or "",
        "attempt_count": repair.attempt_count,
        "total_inches_repaired": _decimal_to_str(repair.total_inches_repaired),
        "last_attempt_date": repair.last_attempt_date.isoformat()
        if repair.last_attempt_date
        else "",
        "assigned_to_id": repair.assigned_to_id,
        "assigned_to_name": _format_user_display(repair.assigned_to),
        "comments": repair.comments or "",
        "created_at": repair.created_at.isoformat() if repair.created_at else "",
        "created_by_id": repair.created_by_id,
        "created_by_name": _format_user_display(repair.created_by),
        "updated_at": repair.updated_at.isoformat() if repair.updated_at else "",
        "updated_by_id": repair.updated_by_id,
        "updated_by_name": _format_user_display(repair.updated_by),
        "closed_at": repair.closed_at.isoformat() if repair.closed_at else "",
        "closed_by_id": repair.closed_by_id,
        "closed_by_name": _format_user_display(repair.closed_by),
        "weld_wps_document_id": weld.wps_document_id,
        "weld_wps_document_name": getattr(weld.wps_document, "name", ""),
        "weld_disposition": weld.disposition,
    }
    if include_children:
        payload["attempts"] = [_serialize_attempt(attempt) for attempt in repair.attempts.all()]
        payload["reinspections"] = [
            _serialize_reinspection(entry) for entry in repair.reinspections.all()
        ]
    return payload


def _parse_optional_decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Invalid decimal value") from exc


def _load_json_body(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON body") from exc


def _ensure_repair_access(request, repair: WeldRepair):
    project = repair.weld.project
    forbidden = _require_project_membership(request, project)
    if forbidden:
        return forbidden
    return None


@require_http_methods(["POST"])
def mark_weld_for_repair(request, org_slug, weld_id: int):
    """Create or retrieve the active repair case for a weld.

    This endpoint mirrors the behaviour of :func:`services.mark_repair_for_weld`
    and returns a serialized repair payload suitable for the weld and repair
    log grids. The request body is optional JSON allowing callers to override
    snapshot metadata such as ``flagged_at`` or ``original_ndereport_ref``.
    """
    weld = get_object_or_404(Weld.objects.select_related("project"), pk=weld_id)
    forbidden = _require_project_membership(request, weld.project)
    if forbidden:
        return forbidden

    try:
        payload = _load_json_body(request)
    except ValueError:
        return HttpResponseBadRequest("Invalid JSON body")

    flagged_at = parse_date(payload.get("flagged_at")) if payload.get("flagged_at") else None
    original_ndereport_ref = payload.get("original_ndereport_ref")
    original_nderig = payload.get("original_nderig")
    defect_code = payload.get("defect_code_snapshot") or payload.get("defect_code")
    nde_type = payload.get("nde_type")
    manual_flag = payload.get("manual_flag")
    if manual_flag is None:
        manual_flag = True

    actor = request.user if getattr(request.user, "is_authenticated", False) else None
    repair, created = mark_repair_for_weld(
        weld,
        flagged_at=flagged_at,
        original_ndereport_ref=original_ndereport_ref,
        original_nderig=original_nderig,
        defect_code=defect_code,
        nde_type=nde_type,
        manual_flag=manual_flag,
        created_by=actor,
    )

    status_code = 201 if created else 200
    include_children = "expand" in request.GET and "attempts" in request.GET.get("expand", "")
    return JsonResponse(
        {
            "repair": _serialize_repair(repair, include_children=include_children),
            "created": created,
        },
        status=status_code,
    )


@require_http_methods(["GET"])
def project_repairs(request, org_slug, project_id: int):
    project = get_object_or_404(Project, pk=project_id)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        return forbidden

    repair_qs = (
        WeldRepair.objects.filter(weld__project=project)
        .select_related("weld", "weld__wps_document", "assigned_to", "created_by", "updated_by", "closed_by")
        .prefetch_related("attempts", "reinspections")
    )

    status_filter = request.GET.get("status")
    if status_filter:
        repair_qs = repair_qs.filter(status=status_filter)

    assigned_to = request.GET.get("assigned_to")
    if assigned_to:
        repair_qs = repair_qs.filter(assigned_to_id=assigned_to)

    weld_identifier = request.GET.get("weld_id")
    if weld_identifier:
        repair_qs = repair_qs.filter(weld__weld_id=weld_identifier)

    welder_filter = request.GET.get("welder_stencil")
    if welder_filter:
        repair_qs = repair_qs.filter(
            Q(weld__primary_stencil__iexact=welder_filter)
            | Q(weld__welder_stencil_repair__icontains=welder_filter)
        )

    flagged_gte = request.GET.get("flagged_at__gte")
    if flagged_gte:
        flagged_date = parse_date(flagged_gte)
        if flagged_date:
            repair_qs = repair_qs.filter(flagged_at__gte=flagged_date)

    flagged_lte = request.GET.get("flagged_at__lte")
    if flagged_lte:
        flagged_date = parse_date(flagged_lte)
        if flagged_date:
            repair_qs = repair_qs.filter(flagged_at__lte=flagged_date)

    original_nderig = request.GET.get("original_nderig")
    if original_nderig:
        repair_qs = repair_qs.filter(original_nderig=original_nderig)

    open_gt_days = request.GET.get("open_gt_days")
    if open_gt_days:
        try:
            days = int(open_gt_days)
        except (TypeError, ValueError):
            days = None
        if days:
            cutoff = date.today() - timedelta(days=days)
            repair_qs = repair_qs.filter(
                Q(reinspection_passed_at__isnull=True)
                & (Q(flagged_at__lte=cutoff) | Q(flagged_at__isnull=True))
            )

    sort_param = request.GET.get("sort")
    allowed_sort_fields = {
        "flagged_at",
        "repair_date",
        "status",
        "attempt_count",
        "total_inches_repaired",
        "reinspection_passed_at",
    }
    if sort_param:
        orderings: list[str] = []
        for raw_field in sort_param.split(","):
            field = raw_field.strip()
            if not field:
                continue
            desc = field.startswith("-")
            base = field[1:] if desc else field
            if base in allowed_sort_fields:
                orderings.append(field)
        if orderings:
            repair_qs = repair_qs.order_by(*orderings)
    else:
        repair_qs = repair_qs.order_by("-flagged_at", "-id")

    per_page = int(request.GET.get("per_page", 25) or 25)
    page_number = int(request.GET.get("page", 1) or 1)
    paginator = Paginator(repair_qs, per_page)
    page_obj = paginator.get_page(page_number)

    expand = request.GET.get("expand", "")
    include_attempts = "attempts" in expand
    include_reinspections = "reinspections" in expand

    repairs_payload = []
    for repair in page_obj.object_list:
        payload = _serialize_repair(
            repair,
            include_children=include_attempts or include_reinspections,
        )
        if include_attempts and "attempts" not in payload:
            payload["attempts"] = [_serialize_attempt(attempt) for attempt in repair.attempts.all()]
        if include_reinspections and "reinspections" not in payload:
            payload["reinspections"] = [
                _serialize_reinspection(entry) for entry in repair.reinspections.all()
            ]
        repairs_payload.append(payload)

    return JsonResponse(
        {
            "data": repairs_payload,
            "page": page_obj.number,
            "total_pages": paginator.num_pages,
            "total_rows": paginator.count,
        }
    )


@require_http_methods(["PATCH"])
def update_repair(request, org_slug, repair_id: int):
    repair = get_object_or_404(
        WeldRepair.objects.select_related("weld", "weld__project", "assigned_to"),
        pk=repair_id,
    )
    forbidden = _ensure_repair_access(request, repair)
    if forbidden:
        return forbidden

    try:
        payload = _load_json_body(request)
    except ValueError:
        return HttpResponseBadRequest("Invalid JSON body")

    allowed_fields = {
        "assigned_to_id",
        "repair_stencil",
        "repair_date",
        "nde_type",
        "original_nderig",
        "comments",
        "status",
    }

    updates = {}
    for key, value in payload.items():
        if key not in allowed_fields:
            continue
        updates[key] = value

    actor = request.user if getattr(request.user, "is_authenticated", False) else None

    if "assigned_to_id" in updates:
        assigned_to_id = updates["assigned_to_id"]
        if assigned_to_id:
            assigned_user = get_object_or_404(User, pk=assigned_to_id)
        else:
            assigned_user = None
        repair.assigned_to = assigned_user

    if "repair_stencil" in updates:
        repair.repair_stencil = updates["repair_stencil"] or ""

    if "repair_date" in updates:
        repair.repair_date = (
            parse_date(updates["repair_date"])
            if updates["repair_date"]
            else None
        )

    if "nde_type" in updates:
        repair.nde_type = updates["nde_type"] or ""

    if "original_nderig" in updates:
        repair.original_nderig = updates["original_nderig"] or ""

    if "comments" in updates:
        repair.comments = updates["comments"] or ""

    if "status" in updates and updates["status"]:
        status_value = updates["status"]
        if status_value == WeldRepair.Status.CLOSED:
            repair.close(closed_by=actor)
        else:
            repair.status = status_value

    repair.updated_by = actor
    repair.save()

    include_children = "expand" in request.GET and "attempts" in request.GET.get("expand", "")
    return JsonResponse({"repair": _serialize_repair(repair, include_children=include_children)})


@require_http_methods(["POST"])
def create_repair_attempt(request, org_slug, repair_id: int):
    repair = get_object_or_404(
        WeldRepair.objects.select_related("weld", "weld__project"), pk=repair_id
    )
    forbidden = _ensure_repair_access(request, repair)
    if forbidden:
        return forbidden

    try:
        payload = _load_json_body(request)
    except ValueError:
        return HttpResponseBadRequest("Invalid JSON body")

    performed_at = parse_date(payload.get("performed_at")) if payload.get("performed_at") else None
    if not performed_at:
        return HttpResponseBadRequest("performed_at is required")

    wps_document = None
    if payload.get("wps_document_id"):
        wps_document = get_object_or_404(FileNode, pk=payload["wps_document_id"])

    inches_value = None
    if "inches_repaired" in payload:
        try:
            inches_value = _parse_optional_decimal(payload.get("inches_repaired"))
        except ValueError:
            return HttpResponseBadRequest("Invalid inches_repaired value")

    actor = request.user if getattr(request.user, "is_authenticated", False) else None
    attempt = RepairAttempt.objects.create(
        repair=repair,
        performed_at=performed_at,
        wps_document=wps_document,
        welder_stencil=payload.get("welder_stencil", ""),
        inches_repaired=inches_value,
        outcome=payload.get("outcome", RepairAttempt.Outcome.FAIL),
        notes=payload.get("notes", ""),
        created_by=actor,
    )

    repair.refresh_from_db()
    return JsonResponse(
        {
            "attempt": _serialize_attempt(attempt),
            "repair": _serialize_repair(repair, include_children=True),
        },
        status=201,
    )


@require_http_methods(["POST"])
def create_repair_reinspection(request, org_slug, repair_id: int):
    repair = get_object_or_404(
        WeldRepair.objects.select_related("weld", "weld__project"), pk=repair_id
    )
    forbidden = _ensure_repair_access(request, repair)
    if forbidden:
        return forbidden

    try:
        payload = _load_json_body(request)
    except ValueError:
        return HttpResponseBadRequest("Invalid JSON body")

    reinspection_date = (
        parse_date(payload.get("reinspection_date"))
        if payload.get("reinspection_date")
        else None
    )
    if not reinspection_date:
        return HttpResponseBadRequest("reinspection_date is required")

    result = payload.get("reinspection_result") or Reinspection.Result.FAIL
    actor = request.user if getattr(request.user, "is_authenticated", False) else None
    entry = Reinspection.objects.create(
        repair=repair,
        reinspection_date=reinspection_date,
        reinspection_nderig=payload.get("reinspection_nderig", ""),
        reinspection_ndereport_ref=payload.get("reinspection_ndereport_ref", ""),
        reinspection_result=result,
        inspector=payload.get("inspector", ""),
        notes=payload.get("notes", ""),
        created_by=actor,
    )

    repair.refresh_from_db()
    return JsonResponse(
        {
            "reinspection": _serialize_reinspection(entry),
            "repair": _serialize_repair(repair, include_children=True),
        },
        status=201,
    )


@require_http_methods(["POST"])
def close_repair(request, org_slug, repair_id: int):
    repair = get_object_or_404(
        WeldRepair.objects.select_related("weld", "weld__project"), pk=repair_id
    )
    forbidden = _ensure_repair_access(request, repair)
    if forbidden:
        return forbidden

    actor = request.user if getattr(request.user, "is_authenticated", False) else None
    try:
        payload = _load_json_body(request)
    except ValueError:
        payload = {}

    closed_at = None
    if payload.get("closed_at"):
        parsed = parse_date(payload.get("closed_at"))
        if parsed:
            closed_at = datetime.combine(parsed, datetime.min.time())

    repair.close(closed_by=actor, closed_at=closed_at)
    repair.refresh_from_db()
    return JsonResponse({"repair": _serialize_repair(repair, include_children=True)})
