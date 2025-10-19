from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from .models import Document
from .forms import DocumentForm
from organizations.decorators import require_membership

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
    return render(request, "documents/detail.html", {"org": request.org, "doc": doc})
