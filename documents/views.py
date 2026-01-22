import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from organizations.decorators import require_membership
from organizations.models import Membership

from welds.forms import MaterialHeatForm
from welds.models import MaterialHeat

from .forms import DocumentForm
from .models import Document

@login_required
@require_membership("MEMBER")  # guests read-only; members+ can upload
def doc_list(request, org_slug):
    docs = (
        Document.objects.filter(org=request.org)
        .select_related("latest_version", "checked_out_by")
        .order_by("doc_type", "number", "-latest_version__version", "name")
    )
    membership = Membership.objects.filter(org=request.org, user=request.user).first()
    if membership and membership.role == Membership.Role.GUEST:
        docs = docs.filter(is_kpi_template=False)
    doc_type_choices = Document.DocType.choices
    context = {
        "org": request.org,
        "docs": docs,
        "doc_type_choices": doc_type_choices,
    }
    return render(request, "documents/list.html", context)

@login_required
@require_membership("MEMBER")
def doc_upload(request, org_slug):
    if request.method == "POST":
        form = DocumentForm(request.POST, request.FILES)
        if form.is_valid():
            doc = form.save(org=request.org, user=request.user)
            if doc.doc_type == Document.DocType.MTR:
                return redirect("doc_detail", org_slug=request.org.slug, doc_id=doc.id)
            return redirect("doc_list", org_slug=request.org.slug)
    else:
        form = DocumentForm()
    return render(request, "documents/upload.html", {"org": request.org, "form": form})

@login_required
@require_membership("GUEST")  # guests allowed to view
def doc_detail(request, org_slug, doc_id):
    doc = get_object_or_404(
        Document.objects.select_related("latest_version", "latest_version__uploaded_by"),
        pk=doc_id,
        org=request.org,
    )
    membership = Membership.objects.filter(org=request.org, user=request.user).first()
    if doc.is_kpi_template and membership and membership.role == Membership.Role.GUEST:
        return HttpResponseForbidden("Guests cannot access KPI templates.")

    material_heat_form = None
    material_heat = None

    if doc.doc_type == "MTR":
        material_heat = (
            MaterialHeat.objects.filter(org=request.org, mtr_document=doc).first()
        )

        if request.method == "POST":
            material_heat_form = MaterialHeatForm(
                request.POST, instance=material_heat
            )
            if material_heat_form.is_valid():
                heat = material_heat_form.save(commit=False)
                heat.org = request.org
                heat.mtr_document = doc
                try:
                    heat.save()
                except IntegrityError:
                    material_heat_form.add_error(
                        "heat_number",
                        "A heat with this number already exists for this organization.",
                    )
                else:
                    messages.success(request, "Material heat details saved.")
                    return redirect("doc_detail", org_slug=request.org.slug, doc_id=doc.id)
        else:
            material_heat_form = MaterialHeatForm(instance=material_heat)
    elif request.method == "POST":
        messages.error(request, "Updates are only supported for MTR documents.")
        return redirect("doc_detail", org_slug=request.org.slug, doc_id=doc.id)

    context = {
        "org": request.org,
        "doc": doc,
        "material_heat_form": material_heat_form,
        "material_heat": material_heat,
    }
    return render(request, "documents/detail.html", context)


@login_required
@require_membership("MEMBER")
@require_POST
def doc_inline_update(request, org_slug, doc_id):
    doc = get_object_or_404(Document, pk=doc_id, org=request.org)

    if request.content_type and "application/json" in request.content_type:
        try:
            payload = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON payload."}, status=400)
    else:
        payload = request.POST

    field = (payload.get("field") or "").strip()
    raw_value = payload.get("value", "")
    value = raw_value.strip()

    editable_fields = {"number", "name", "doc_type"}
    if field not in editable_fields:
        return JsonResponse({"error": "This field cannot be updated inline."}, status=400)

    if field in {"number", "name"} and not value:
        return JsonResponse({"error": "This field cannot be empty."}, status=400)

    if field == "doc_type":
        # Normalise casing and validate against declared choices.
        value = value.upper()
        valid_doc_types = {choice for choice, _ in Document.DocType.choices if choice}
        if value and value not in valid_doc_types:
            return JsonResponse({"error": "Unknown document type."}, status=400)

    setattr(doc, field, value)

    try:
        doc.save(update_fields=[field])
    except ValidationError as exc:
        return JsonResponse({"error": "; ".join(exc.messages)}, status=400)

    display_value = value or "—"
    return JsonResponse({"value": value, "display": display_value})
