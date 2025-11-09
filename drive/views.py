import json
import mimetypes
import os

from django.conf import settings
from django.contrib import messages
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from django.http import (
    FileResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseRedirect,
    JsonResponse,
)
from django.utils.encoding import iri_to_uri
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.core.exceptions import ValidationError

from organizations.models import Membership
from organizations.decorators import require_membership
from .models import Folder, FileNode, FileVersion, FileEvent
from projects.utils import get_project_for_request, user_has_project_access
from .utils import log_file_event, sanitize_filename, validate_upload
from django.core.paginator import Paginator, EmptyPage

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
    project = folder.project

    child_folders = (
        Folder.objects.filter(org=org, parent=folder)
        .select_related("project")
        .order_by("name")
    )

    files = (
        FileNode.objects.filter(org=org, folder=folder)
        .select_related("latest_version", "checked_out_by")
        .order_by("doc_type", "number", "name")
    )

    allowed_list = sorted(list(getattr(settings, "ALLOWED_CONTENT_TYPES", [])))
    context = {
        "org": org,
        "project": project,
        "folder": folder,
        "child_folders": child_folders,
        "files": files,
        "doc_type_choices": FileNode.DocType.choices,
        "max_upload_mb": getattr(settings, "MAX_UPLOAD_SIZE_MB", 50),
        "allowed_types": allowed_list,
        "allowed_types_json": json.dumps(allowed_list),
        "upload_url": reverse(
            "drive_upload",
            kwargs={
                "org_slug": org.slug,
                "project_slug": project.slug,
                "folder_id": folder.id,
            },
        ),
    }
    return render(request, "drive/folder_view.html", context)


@login_required
@require_membership("MEMBER")
@require_POST
def file_inline_update(request, org_slug, project_slug, file_id):
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
        value = value.upper()
        valid_doc_types = {choice for choice, _ in FileNode.DocType.choices if choice}
        if value and value not in valid_doc_types:
            return JsonResponse({"error": "Unknown document type."}, status=400)

    setattr(node, field, value)

    try:
        node.save(update_fields=[field])
    except ValidationError as exc:
        return JsonResponse({"error": "; ".join(exc.messages)}, status=400)

    display_value = value or "—"
    if field == "doc_type":
        display_value = node.get_doc_type_display() or value or "—"

    return JsonResponse({"value": value, "display": display_value})

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
    note = (request.POST.get("note") or "").strip()[:500]

    if not upload:
        messages.error(request, "No file provided.")
        return redirect("drive_folder", org_slug=org.slug, project_slug=folder.project.slug, folder_id=folder.id)

    # Validate size + MIME
    ok, content_type, err = validate_upload(upload, settings.ALLOWED_CONTENT_TYPES, settings.MAX_UPLOAD_SIZE_MB)
    if not ok:
        messages.error(request, err)
        return redirect("drive_folder", org_slug=org.slug, project_slug=folder.project.slug, folder_id=folder.id)

    # Keep original user-facing display name
    display_name = upload.name

    # Sanitize stored filename (affects storage key only)
    upload.name = sanitize_filename(upload.name)

    # Derive node slug from display name stem (so doc.pdf & doc.DOCX share)
    stem, _ext = os.path.splitext(display_name)
    node_slug = slugify(stem)

    with transaction.atomic():
        try:
            node = (
                FileNode.objects.select_for_update()
                .get(org=org, folder=folder, slug=node_slug)
            )

            # --- ENFORCE CHECKOUT (from Step 3) ---
            if node.checked_out_by_id and node.checked_out_by_id != request.user.id:
                messages.error(request, "This file is checked out by another user. You cannot upload a new version.")
                return redirect("drive_folder", org_slug=org.slug, project_slug=folder.project.slug, folder_id=folder.id)

            latest = node.versions.order_by("-version").first()
            next_version = (latest.version if latest else 0) + 1
        except FileNode.DoesNotExist:
            node = FileNode(
                org=org,
                project=folder.project,
                folder=folder,
                name=display_name,     # user-facing
                slug=node_slug,
                size=0,
                created_by=request.user,
            )
            node.save()
            next_version = 1

        fv = FileVersion.objects.create(
            file_node=node,
            version=next_version,
            blob=upload,              # sanitized name used for storage key
            size=upload.size,
            content_type=content_type,
            uploaded_by=request.user,
            note=note,                # <-- NEW
        )

        node.size = fv.size
        node.name = display_name      # always show latest uploaded display name
        node.save(update_fields=["size", "name"])

    try:
        # Audit already wired; ensure UPLOAD is logged with the new version ref
        log_file_event(request, node, FileEvent.Action.UPLOAD, fv)
    except Exception:
        pass

    messages.success(request, f"Uploaded {display_name} as v{next_version}.")
    return redirect("drive_folder", org_slug=org.slug, project_slug=folder.project.slug, folder_id=folder.id)

from organizations.models import Membership

@login_required
@require_membership("GUEST")
def file_detail(request, org_slug, project_slug, file_id):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_access(request, project)
    if forbidden:
        return forbidden

    node = get_object_or_404(
        FileNode.objects.select_related("latest_version", "folder", "project", "org", "created_by"),
        pk=file_id, org=request.org, project=project
    )
    versions = node.versions.all().order_by("-version")

    # Log view (safe)
    try:
        log_file_event(request, node, FileEvent.Action.VIEW)
    except Exception:
        pass

    # ---- Compute role/permissions for template (NO queryset calls in template) ----
    user_role = None
    try:
        m = Membership.objects.get(org=request.org, user=request.user)
        user_role = m.role  # "GUEST","VIEWER","MEMBER","ADMIN","OWNER"
    except Membership.DoesNotExist:
        user_role = None

    is_admin_or_owner = user_role in (Membership.Role.ADMIN, Membership.Role.OWNER)
    is_member = user_role in (Membership.Role.MEMBER, Membership.Role.ADMIN, Membership.Role.OWNER)
    is_creator = (node.created_by_id == request.user.id)

    # Activity tab: only member+ or creator
    can_view_activity = bool(is_member or is_creator)

    # Checkout-related convenience flags
    is_checked_out = bool(node.checked_out_by_id)
    is_checked_out_by_me = (node.checked_out_by_id == request.user.id)
    can_checkout = is_member and (not is_checked_out or is_checked_out_by_me)
    can_checkin = is_member and is_checked_out_by_me
    can_force_checkin = is_admin_or_owner and is_checked_out

    # Activity pagination (only if allowed)
    events_page = None
    if can_view_activity:
        from django.core.paginator import Paginator, EmptyPage
        page = int(request.GET.get("apage", "1") or 1)
        qs = node.events.select_related("actor", "version").order_by("-at")
        paginator = Paginator(qs, 10)
        try:
            events_page = paginator.page(page)
        except EmptyPage:
            events_page = paginator.page(paginator.num_pages)

    return render(
        request,
        "drive/file_detail.html",
        {
            "org": request.org,
            "project": project,
            "node": node,
            "versions": versions,
            "events_page": events_page,
            "can_view_activity": can_view_activity,

            # expose simple flags ONLY (safe for templates)
            "is_admin_or_owner": is_admin_or_owner,
            "is_member": is_member,
            "is_creator": is_creator,
            "is_checked_out": is_checked_out,
            "is_checked_out_by_me": is_checked_out_by_me,
            "can_checkout": can_checkout,
            "can_checkin": can_checkin,
            "can_force_checkin": can_force_checkin,
        },
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
    try:
        log_file_event(request, node, FileEvent.Action.DOWNLOAD, node.latest_version)
    except Exception:
        pass
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
    try:
        log_file_event(request, node, FileEvent.Action.DOWNLOAD, fv)
    except Exception:
        pass
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
    # inside file_stream_latest, after node/latest_version checks and before return
    try:
        log_file_event(request, node, FileEvent.Action.PREVIEW, node.latest_version)
    except Exception:
        pass

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
    try:
        log_file_event(request, node, FileEvent.Action.PREVIEW, fv)
    except Exception:
        pass
    return resp

@login_required
@require_membership("MEMBER")
@require_POST
def file_checkout(request, org_slug, project_slug, file_id):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_access(request, project)
    if forbidden: return forbidden

    node = get_object_or_404(
        FileNode.objects.select_for_update(),
        pk=file_id, org=request.org, project=project
    )

    with transaction.atomic():
        # Reload with lock
        node = FileNode.objects.select_for_update().get(pk=node.pk)
        if node.checked_out_by_id and node.checked_out_by_id != request.user.id:
            messages.error(request, "This file is already checked out by another user.")
        else:
            node.checked_out_by = request.user
            node.checked_out_at = timezone.now()
            node.save(update_fields=["checked_out_by", "checked_out_at"])
            messages.success(request, "File checked out. Only you can upload new versions.")
    node.checked_out_by = request.user
    node.checked_out_at = timezone.now()
    node.save(update_fields=["checked_out_by", "checked_out_at"])
    try:
        log_file_event(request, node, FileEvent.Action.CHECKOUT)
    except Exception:
        pass
    messages.success(request, "File checked out. Only you can upload new versions.")

    return redirect("drive_file", org_slug=request.org.slug, project_slug=project.slug, file_id=node.id)


@login_required
@require_membership("MEMBER")
@require_POST
def file_checkin(request, org_slug, project_slug, file_id):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_access(request, project)
    if forbidden: return forbidden

    node = get_object_or_404(
        FileNode.objects.select_for_update(),
        pk=file_id, org=request.org, project=project
    )

    with transaction.atomic():
        node = FileNode.objects.select_for_update().get(pk=node.pk)
        if not node.checked_out_by_id:
            messages.info(request, "This file is not currently checked out.")
        elif node.checked_out_by_id != request.user.id:
            messages.error(request, "Only the user who checked out this file can check it back in.")
        else:
            node.checked_out_by = None
            node.checked_out_at = None
            node.save(update_fields=["checked_out_by", "checked_out_at"])
            messages.success(request, "File checked in. Others can upload new versions again.")
    node.checked_out_by = None
    node.checked_out_at = None
    node.save(update_fields=["checked_out_by", "checked_out_at"])
    try:
        log_file_event(request, node, FileEvent.Action.CHECKIN)
    except Exception:
        pass
    messages.success(request, "File checked in. Others can upload new versions again.")

    return redirect("drive_file", org_slug=request.org.slug, project_slug=project.slug, file_id=node.id)


@login_required
@require_membership("ADMIN")  # ADMIN+ can override
@require_POST
def file_force_checkin(request, org_slug, project_slug, file_id):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_access(request, project)
    if forbidden: return forbidden

    node = get_object_or_404(
        FileNode.objects.select_for_update(),
        pk=file_id, org=request.org, project=project
    )

    with transaction.atomic():
        node = FileNode.objects.select_for_update().get(pk=node.pk)
        node.checked_out_by = None
        node.checked_out_at = None
        node.save(update_fields=["checked_out_by", "checked_out_at"])
        messages.success(request, "Force check-in complete. Others can upload new versions.")
    node.checked_out_by = None
    node.checked_out_at = None
    node.save(update_fields=["checked_out_by", "checked_out_at"])
    try:
        log_file_event(request, node, FileEvent.Action.FORCE_CHECKIN)
    except Exception:
        pass
    messages.success(request, "Force check-in complete. Others can upload new versions.")

    return redirect("drive_file", org_slug=request.org.slug, project_slug=project.slug, file_id=node.id)