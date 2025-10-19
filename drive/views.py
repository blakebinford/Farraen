import mimetypes
import os

from django.contrib import messages
from django.urls import reverse
from django.db.models import OuterRef, Subquery, Max
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from django.http import (
    FileResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
)
from django.utils.encoding import iri_to_uri
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.clickjacking import xframe_options_sameorigin

from accounts.models import User
from organizations.decorators import require_membership
from .models import Folder, FileNode, FileVersion
from projects.utils import get_project_for_request, user_has_project_access


# ---------- helpers ----------

def _require_project_access(request, project):
    if not user_has_project_access(request.user, project):
        return HttpResponseForbidden("No project access")
    return None


def _breadcrumb_for(folder: Folder):
    # Walk up parents to root, then reverse for UI
    trail = []
    cur = folder
    while cur is not None:
        trail.append(cur)
        cur = cur.parent
    trail.reverse()
    return trail


# ---------- views ----------

@login_required
@require_membership("GUEST")  # guests can view
def project_drive_root(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_access(request, project)
    if forbidden:
        return forbidden

    roots = (
        project.folders
        .filter(parent__isnull=True, is_archived=False)
        .select_related("org", "project")
        .order_by("name")
    )
    return render(
        request,
        "drive/folder_root.html",
        {"org": request.org, "project": project, "roots": roots},
    )


@login_required
@require_membership("GUEST")  # guests can view
def folder_view(request, org_slug, project_slug, folder_id):
    org = request.org
    folder = get_object_or_404(Folder, pk=folder_id, org=org)

    # Prefetch/annotate latest version metadata for files
    latest_version_qs = FileVersion.objects.filter(file_node=OuterRef("pk")).order_by("-version")
    files_qs = (
        FileNode.objects.filter(org=org, folder=folder)
        .select_related("project", "folder")
        .annotate(
            latest_version_num=Subquery(latest_version_qs.values("version")[:1]),
            latest_size=Subquery(latest_version_qs.values("size")[:1]),
            latest_ct=Subquery(latest_version_qs.values("content_type")[:1]),
            latest_when=Subquery(latest_version_qs.values("created_at")[:1]),
            latest_who_id=Subquery(latest_version_qs.values("uploaded_by_id")[:1]),
        )
    )

    # You may also want to fetch the users (for latest_who display)
    user_map = {u.id: u for u in User.objects.filter(id__in=files_qs.values_list("latest_who_id", flat=True))}

    # Build entry list
    entries = []

    # Folders first
    child_folders = Folder.objects.filter(org=org, parent=folder).order_by("name")
    for f in child_folders:
        entries.append({
            "kind": "folder",
            "name": f.name,
            "icon": "folder",
            "size": None,
            "content_type": "folder",
            "modified_at": getattr(f, "updated_at", None) or getattr(f, "created_at", None),
            "modified_by": "",
            "href": reverse("drive_folder", kwargs={
                "org_slug": org.slug,
                "project_slug": f.project.slug,
                "folder_id": f.id
            }),
            "tooltip": "Open folder",
        })

    # Then files, ordered by name (or whatever you prefer)
    for n in files_qs.order_by("name"):
        modified_by = ""
        if n.latest_who_id and n.latest_who_id in user_map:
            u = user_map[n.latest_who_id]
            modified_by = u.get_full_name() or u.email or u.username

        entries.append({
            "kind": "file",
            "name": n.name,
            "icon": n.latest_ct or "",  # content-type; mapped by template filter
            "size": n.latest_size or n.size,
            "content_type": n.latest_ct or "",
            "modified_at": n.latest_when,
            "modified_by": modified_by,
            "href": reverse("drive_file", kwargs={  # <-- make files clickable
                "org_slug": org.slug,
                "project_slug": folder.project.slug,
                "file_id": n.id,
            }),
            "download_url": reverse("drive_file_download", kwargs={  # quick download
                "org_slug": org.slug,
                "project_slug": folder.project.slug,
                "file_id": n.id,
            }),
            "tooltip": f"{(n.latest_size or n.size or 0)} bytes",
        })

    context = {
        "org": org,
        "project": folder.project,
        "folder": folder,
        "entries": entries,
    }
    return render(request, "drive/folder_view.html", context)


@login_required
@require_membership("MEMBER")  # members+ can create
def folder_create(request, org_slug, project_slug, folder_id):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_access(request, project)
    if forbidden:
        return forbidden

    parent = get_object_or_404(
        Folder, pk=folder_id, org=request.org, project=project
    )

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        if not name:
            return HttpResponseBadRequest("Name required")
        Folder.objects.create(
            org=request.org,
            project=project,
            name=name,
            parent=parent,
            created_by=request.user,
        )
        return redirect("drive_folder", org_slug=request.org.slug, project_slug=project.slug, folder_id=parent.id)

    return render(
        request,
        "drive/folder_create.html",
        {"org": request.org, "project": project, "parent": parent},
    )


@require_POST
@require_membership(min_role="MEMBER")
def file_upload(request, org_slug, project_slug, folder_id):
    org = request.org
    folder = get_object_or_404(Folder, pk=folder_id, org=org)

    upload = request.FILES.get("file")
    if not upload:
        messages.error(request, "No file provided.")
        return redirect("drive_folder", org_slug=org.slug, project_slug=folder.project.slug, folder_id=folder.id)

    # Keep original display name; slug from stem so "doc.pdf" and "doc.DOCX" share a node
    stem, ext = os.path.splitext(upload.name)
    display_name = upload.name
    node_slug = slugify(stem)

    content_type = upload.content_type or mimetypes.guess_type(upload.name)[0] or "application/octet-stream"

    with transaction.atomic():
        try:
            # Lock if exists so concurrent uploads bump version safely
            node = (
                FileNode.objects.select_for_update()
                .get(org=org, folder=folder, slug=node_slug)
            )
            latest = node.versions.order_by("-version").first()
            next_version = (latest.version if latest else 0) + 1
        except FileNode.DoesNotExist:
            # Create new node starting at v1
            node = FileNode(
                org=org,
                project=folder.project,
                folder=folder,
                name=display_name,
                slug=node_slug,
                size=0,
            )
            node.save()
            next_version = 1

        fv = FileVersion.objects.create(
            file_node=node,
            version=next_version,
            blob=upload,
            size=upload.size,
            content_type=content_type,
            uploaded_by=request.user,
        )

        # Optional: keep node denormalized with latest metadata
        node.size = fv.size
        node.name = display_name  # show latest uploaded name
        node.save(update_fields=["size", "name",])

    messages.success(request, f"Uploaded {display_name} as v{next_version}.")
    return redirect("drive_folder", org_slug=org.slug, project_slug=folder.project.slug, folder_id=folder.id)


@login_required
@require_membership("GUEST")  # guests can view
def file_detail(request, org_slug, project_slug, file_id):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_access(request, project)
    if forbidden:
        return forbidden

    node = get_object_or_404(
        FileNode.objects.select_related("latest_version", "folder", "project", "org"),
        pk=file_id,
        org=request.org,
        project=project,
    )
    versions = node.versions.all().order_by("-version")

    return render(
        request,
        "drive/file_detail.html",
        {"org": request.org, "project": project, "node": node, "versions": versions},
    )


@login_required
@require_membership("GUEST")  # guests can download (read-only)
def file_download_latest(request, org_slug, project_slug, file_id):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_access(request, project)
    if forbidden:
        return forbidden

    node = get_object_or_404(
        FileNode.objects.select_related("latest_version"),
        pk=file_id,
        org=request.org,
        project=project,
    )
    if not node.latest_version:
        return HttpResponseBadRequest("No content")
    return FileResponse(
        node.latest_version.blob.open("rb"),
        as_attachment=True,
        filename=node.name,
    )


@login_required
@require_membership("GUEST")  # guests can download specific versions
def file_download_version(request, org_slug, project_slug, file_id, version):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_access(request, project)
    if forbidden:
        return forbidden

    node = get_object_or_404(
        FileNode, pk=file_id, org=request.org, project=project
    )
    fv = get_object_or_404(FileVersion, file_node=node, version=version)
    return FileResponse(
        fv.blob.open("rb"),
        as_attachment=True,
        filename=f"{version}_{node.name}",
    )

@login_required
@require_membership("GUEST")
@xframe_options_sameorigin
def file_stream_latest(request, org_slug, project_slug, file_id):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_access(request, project)
    if forbidden:
        return forbidden

    node = get_object_or_404(
        FileNode.objects.select_related("latest_version"),
        pk=file_id, org=request.org, project=project
    )
    if not node.latest_version:
        return HttpResponseBadRequest("No content")

    f = node.latest_version.blob.open("rb")
    resp = FileResponse(f, as_attachment=False, filename=node.name)
    # Force inline so browsers can preview
    resp["Content-Disposition"] = f'inline; filename="{iri_to_uri(node.name)}"'
    resp["Content-Type"] = node.latest_version.content_type or "application/octet-stream"
    return resp


@login_required
@require_membership("GUEST")
@xframe_options_sameorigin
def file_stream_version(request, org_slug, project_slug, file_id, version):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_access(request, project)
    if forbidden:
        return forbidden

    node = get_object_or_404(FileNode, pk=file_id, org=request.org, project=project)
    fv = get_object_or_404(FileVersion, file_node=node, version=version)

    f = fv.blob.open("rb")
    resp = FileResponse(f, as_attachment=False, filename=node.name)
    resp["Content-Disposition"] = f'inline; filename="{iri_to_uri(node.name)}"'
    resp["Content-Type"] = fv.content_type or "application/octet-stream"
    return resp