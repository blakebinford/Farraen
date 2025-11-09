from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.shortcuts import render, redirect, get_object_or_404

from organizations.decorators import require_membership

from welds.forms import MaterialHeatForm
from welds.models import MaterialHeat

from .forms import DocumentForm
from .models import Document

@login_required
@require_membership("MEMBER")  # guests read-only; members+ can upload
def doc_list(request, org_slug):
    docs = Document.objects.filter(org=request.org).order_by("doc_type","number","-version")
    return render(request, "documents/list.html", {"org": request.org, "docs": docs})

@login_required
@require_membership("MEMBER")
def doc_upload(request, org_slug):
    if request.method == "POST":
        form = DocumentForm(request.POST, request.FILES)
        if form.is_valid():
            base = form.save(commit=False)
            base.org = request.org
            base.uploaded_by = request.user
            # auto-increment version when same (org, type, number) exists
            latest = (
                Document.objects.filter(org=request.org, doc_type=base.doc_type, number=base.number)
                .order_by("-version")
                .first()
            )
            base.version = 1 if not latest else latest.version + 1
            base.save()
            return redirect("doc_list", org_slug=request.org.slug)
    else:
        form = DocumentForm()
    return render(request, "documents/upload.html", {"org": request.org, "form": form})

@login_required
@require_membership("GUEST")  # guests allowed to view
def doc_detail(request, org_slug, doc_id):
    doc = get_object_or_404(Document, pk=doc_id, org=request.org)

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
