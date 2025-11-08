import json

from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from organizations.decorators import require_membership
from projects.utils import get_project_for_request, user_has_project_access

from .models import Weld


def _require_project_membership(request, project):
    if not user_has_project_access(request.user, project):
        return HttpResponseForbidden("No project access")
    return None


def _serialize_weld(weld: Weld) -> dict:
    return {
        "id": weld.id,
        "weld_id": weld.weld_id,
        "joint_type": weld.joint_type,
        "location": weld.location,
        "wps": weld.wps,
        "welder": weld.welder,
        "heat_number": weld.heat_number,
        "status": weld.status,
        "notes": weld.notes,
        "created_at": weld.created_at.isoformat(),
        "updated_at": weld.updated_at.isoformat(),
    }


@require_membership("GUEST")
def weld_log(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        return forbidden

    context = {
        "org": request.org,
        "project": project,
        "data_url": reverse("weld_log_data", kwargs={
            "org_slug": request.org.slug,
            "project_slug": project.slug,
        }),
    }
    return render(request, "welds/weld_log.html", context)


@require_membership("GUEST")
@require_http_methods(["GET", "POST"])
def weld_log_data(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_membership(request, project)
    if forbidden:
        return forbidden

    if request.method == "GET":
        rows = [_serialize_weld(w) for w in project.welds.all()]
        return JsonResponse({"rows": rows})

    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON body")

    weld_id = payload.get("weld_id", "").strip()
    if not weld_id:
        return JsonResponse({"error": "Weld ID is required."}, status=400)

    attrs = {
        "joint_type": payload.get("joint_type", "").strip(),
        "location": payload.get("location", "").strip(),
        "wps": payload.get("wps", "").strip(),
        "welder": payload.get("welder", "").strip(),
        "heat_number": payload.get("heat_number", "").strip(),
        "status": payload.get("status", Weld.Status.PLANNED),
        "notes": payload.get("notes", "").strip(),
    }

    if attrs["status"] not in Weld.Status.values:
        return JsonResponse({"error": "Invalid weld status."}, status=400)

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
