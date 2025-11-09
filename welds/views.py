import json
from decimal import Decimal, InvalidOperation

from django.db import connection
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from organizations.decorators import require_membership
from projects.utils import get_project_for_request, user_has_project_access

from .models import MaterialHeat, NDERig, Weld


def _existing_table_names():
    return set(connection.introspection.table_names())


def _missing_weld_tables():
    existing = _existing_table_names()
    required = {
        MaterialHeat._meta.db_table: "material heat records",
        NDERig._meta.db_table: "NDE rigs",
        Weld._meta.db_table: "weld log entries",
    }
    return [label for table, label in required.items() if table not in existing]


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
    return None


def _decimal_to_str(value):
    if value is None:
        return ""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _serialize_heat(prefix: str, weld: Weld) -> dict:
    heat = getattr(weld, f"{prefix}_heat")
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
    }


def _serialize_weld(weld: Weld) -> dict:
    payload = {
        "id": weld.id,
        "weld_id": weld.weld_id,
        "nde_number": weld.nde_number,
        "drawing_number": weld.drawing_number,
        "weld_type": weld.weld_type,
        "date_welded": weld.date_welded.isoformat() if weld.date_welded else "",
        "welder_stencil_root_hotpass": weld.welder_stencil_root_hotpass,
        "welder_stencil_fill": weld.welder_stencil_fill,
        "welder_stencil_fill_additional": weld.welder_stencil_fill_additional,
        "welder_stencil_cap": weld.welder_stencil_cap,
        "nde_date": weld.nde_date.isoformat() if weld.nde_date else "",
        "nde_rig_id": weld.nde_rig_id,
        "nde_rig_name": weld.nde_rig.name if weld.nde_rig else "",
        "disposition": weld.disposition,
        "disposition_comment": weld.disposition_comment,
        "created_at": weld.created_at.isoformat(),
        "updated_at": weld.updated_at.isoformat(),
    }
    payload.update(_serialize_heat("material1", weld))
    payload.update(_serialize_heat("material2", weld))
    return payload


@require_membership("GUEST")
def weld_log(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        return forbidden

    missing_tables = _missing_weld_tables()
    setup_error = _migrations_required_message(missing_tables)

    context = {
        "org": request.org,
        "project": project,
        "setup_error": setup_error,
    }

    if not setup_error:
        context.update(
            {
                "data_url": reverse(
                    "weld_log_data",
                    kwargs={
                        "org_slug": request.org.slug,
                        "project_slug": project.slug,
                    },
                ),
                "heat_options_url": reverse(
                    "weld_material_heat_options",
                    kwargs={
                        "org_slug": request.org.slug,
                        "project_slug": project.slug,
                    },
                ),
                "nde_rigs_url": reverse(
                    "weld_nde_rig_options",
                    kwargs={
                        "org_slug": request.org.slug,
                        "project_slug": project.slug,
                    },
                ),
            }
        )

    status = 503 if setup_error else 200
    return render(request, "welds/weld_log.html", context, status=status)


@require_membership("GUEST")
@require_http_methods(["GET", "POST"])
def weld_log_data(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        return forbidden

    missing_tables = _missing_weld_tables()
    if missing_tables:
        message = _migrations_required_message(missing_tables)
        return JsonResponse({"error": message}, status=503)

    if request.method == "GET":
        rows = [
            _serialize_weld(w)
            for w in project.welds.select_related(
                "material1_heat", "material2_heat", "nde_rig"
            )
        ]
        return JsonResponse({"rows": rows})

    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON body")

    weld_id = payload.get("weld_id", "").strip()
    if not weld_id:
        return JsonResponse({"error": "Weld ID is required."}, status=400)

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
        return JsonResponse(
            {"error": f"Invalid numeric value for {exc.args[0].replace('_', ' ')}."},
            status=400,
        )

    try:
        date_welded = _parse_date_field("date_welded")
        nde_date = _parse_date_field("nde_date")
    except ValueError as exc:
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
            return JsonResponse(
                {"error": "Invalid Material 2 heat selection."}, status=400
            )

    nde_rig_id = payload.get("nde_rig_id")
    nde_rig = None
    if nde_rig_id:
        try:
            nde_rig = NDERig.objects.get(
                pk=nde_rig_id, org=request.org, is_active=True
            )
        except NDERig.DoesNotExist:
            return JsonResponse({"error": "Invalid NDE rig."}, status=400)

    disposition = payload.get("disposition", Weld.Disposition.ACCEPTED)
    if disposition not in Weld.Disposition.values:
        return JsonResponse({"error": "Invalid weld disposition."}, status=400)

    disposition_comment = _clean_text("disposition_comment")
    if disposition in {Weld.Disposition.REPAIR, Weld.Disposition.CUT_OUT} and not disposition_comment:
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
        "weld_type": _clean_text("weld_type"),
        "date_welded": date_welded,
        "welder_stencil_root_hotpass": _clean_text("welder_stencil_root_hotpass"),
        "welder_stencil_fill": _clean_text("welder_stencil_fill"),
        "welder_stencil_fill_additional": _clean_text(
            "welder_stencil_fill_additional"
        ),
        "welder_stencil_cap": _clean_text("welder_stencil_cap"),
        "nde_date": nde_date,
        "nde_rig": nde_rig,
        "disposition": disposition,
        "disposition_comment": disposition_comment,
    }

    existing_qs = project.welds.filter(weld_id__iexact=weld_id)

    weld_pk = payload.get("id")
    if weld_pk:
        weld = get_object_or_404(Weld, pk=weld_pk, project=project)
        if existing_qs.exclude(pk=weld.pk).exists():
            return JsonResponse({"error": "Weld ID already exists for this project."}, status=400)
        weld.weld_id = weld_id
        for field, value in attrs.items():
            setattr(weld, field, value)
        weld.updated_by = request.user
        weld.save()
        return JsonResponse({"weld": _serialize_weld(weld)})

    if existing_qs.exists():
        return JsonResponse({"error": "Weld ID already exists for this project."}, status=400)

    weld = Weld.objects.create(
        project=project,
        weld_id=weld_id,
        created_by=request.user,
        updated_by=request.user,
        **attrs,
    )
    return JsonResponse({"weld": _serialize_weld(weld)}, status=201)


@require_membership("GUEST")
def material_heat_options(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        return forbidden

    missing_tables = _missing_weld_tables()
    if missing_tables:
        message = _migrations_required_message(missing_tables)
        return JsonResponse({"error": message}, status=503)

    heats = MaterialHeat.objects.filter(org=request.org, is_active=True).order_by(
        "heat_number"
    )
    data = []
    for heat in heats:
        data.append(
            {
                "id": heat.id,
                "heat_number": heat.heat_number,
                "description": heat.description,
                "material_grade": heat.material_grade,
                "outer_diameter_in": _decimal_to_str(heat.outer_diameter_in),
                "wall_thickness_in": _decimal_to_str(heat.wall_thickness_in),
                "wps_number": heat.wps_number,
            }
        )
    return JsonResponse({"heats": data})


@require_membership("GUEST")
def nde_rig_options(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        return forbidden

    missing_tables = _missing_weld_tables()
    if missing_tables:
        message = _migrations_required_message(missing_tables)
        return JsonResponse({"error": message}, status=503)

    rigs = NDERig.objects.filter(org=request.org, is_active=True).order_by("name")
    data = [
        {
            "id": rig.id,
            "name": rig.name,
        }
        for rig in rigs
    ]
    return JsonResponse({"nde_rigs": data})
