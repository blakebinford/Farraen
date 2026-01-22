import json
import mimetypes
import os
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Max
from django.utils.text import slugify
from django.views.decorators.http import require_POST, require_GET
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
from django.template.response import TemplateResponse
from django.core.files.base import ContentFile

from organizations.models import Membership
from organizations.decorators import require_membership
from .models import Folder, FileNode, FileVersion, FileEvent
from django.core.exceptions import PermissionDenied

from projects.permissions import (
    can_edit_drive,
    can_view_project,
    is_org_guest,
    is_project_guest,
)
from projects.utils import (
    assert_project_not_archived,
    get_project_for_request,
)
from .utils import log_file_event, sanitize_filename, validate_upload
from django.core.paginator import Paginator, EmptyPage

# ---------- helpers ----------

def _require_project_access(request, project):
    if is_org_guest(request.user, project.org) or is_project_guest(request.user, project):
        return HttpResponseForbidden("Guests do not have access to Drive.")
    if not can_view_project(request.user, project):
        return HttpResponseForbidden("No project access")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if not can_edit_drive(request.user, project):
            return HttpResponseForbidden("Drive access is read-only for this role.")
        try:
            assert_project_not_archived(project)
        except PermissionDenied:
            return HttpResponseForbidden("This project is archived and locked.")
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


def _recently_viewed_files(request, project, limit=5):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return []

    org = getattr(request, "org", None) or project.org

    events = FileEvent.objects.filter(
        org=org,
        actor=user,
        action=FileEvent.Action.VIEW,
        file_node__project=project,
        file_node__is_archived=False,
    )
    if is_org_guest(user, org) or is_project_guest(user, project):
        events = events.filter(file_node__is_kpi_template=False)

    events = (
        events.values("file_node_id")
        .annotate(last_at=Max("at"))
        .order_by("-last_at")
    )

    rows = list(events[:limit])
    if not rows:
        return []

    node_ids = [row["file_node_id"] for row in rows]
    nodes = (
        FileNode.objects.filter(id__in=node_ids)
        .select_related("latest_version")
    )
    nodes_by_id = {node.id: node for node in nodes}

    results = []
    for row in rows:
        node = nodes_by_id.get(row["file_node_id"])
        if node:
            results.append({
                "node": node,
                "last_at": row["last_at"],
            })
    return results


def _block_kpi_template(request, project, node):
    if not getattr(node, "is_kpi_template", False):
        return None
    if is_org_guest(request.user, project.org) or is_project_guest(request.user, project):
        return HttpResponseForbidden("Guests cannot access KPI templates.")
    return None


# ---------- views ----------

@login_required
@require_membership("GUEST")  # guests can view
def project_drive_root(request, org_slug, project_slug):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_access(request, project)
    if forbidden:
        return forbidden

    active_filter = (request.GET.get("filter") or "").strip().lower()
    active_doc_types = [value.upper() for value in request.GET.getlist("doc_type") if value]

    roots = (
        project.folders
        .filter(parent__isnull=True, is_archived=False)
        .select_related("org", "project")
        .order_by("name")
    )
    folder_tree = (
        project.folders.filter(is_archived=False)
        .select_related("parent")
        .order_by("path")
    )
    return render(
        request,
        "drive/folder_root.html",
        {
            "org": request.org,
            "project": project,
            "roots": roots,
            "folder_tree": folder_tree,
            "breadcrumb": [],
            "active_filter": active_filter,
            "active_doc_types": active_doc_types,
            "recently_viewed": _recently_viewed_files(request, project),
            "can_edit_drive": can_edit_drive(request.user, project),
        },
    )


@login_required
@require_membership("GUEST")  # guests can view
def folder_view(request, org_slug, project_slug, folder_id):
    org = request.org
    project = get_project_for_request(request, org, project_slug)
    folder = get_object_or_404(Folder, pk=folder_id, org=org, project=project)

    forbidden = _require_project_access(request, project)
    if forbidden:
        return forbidden

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

    active_filter = (request.GET.get("filter") or "").strip().lower()

    doc_types = request.GET.getlist("doc_type")
    if not doc_types and request.GET.get("doc_type"):
        doc_types = [request.GET.get("doc_type")]
    doc_types = [value.upper() for value in doc_types if value]
    if doc_types:
        files = files.filter(doc_type__in=doc_types)

    needs_distinct = False

    if active_filter == "trash":
        files = files.filter(is_archived=True)
    else:
        files = files.filter(is_archived=False)

    if active_filter == "checkedout":
        files = files.filter(checked_out_by__isnull=False)
    elif active_filter == "recent":
        cutoff = timezone.now() - timedelta(days=30)
        files = files.filter(
            Q(latest_version__created_at__gte=cutoff)
            | Q(versions__created_at__gte=cutoff)
        )
        needs_distinct = True
    elif active_filter == "starred":
        if hasattr(FileNode, "stars"):
            files = files.filter(stars__user=request.user)
            needs_distinct = True
        else:
            files = files.none()
    elif active_filter == "trash":
        # already filtered above, keep archived ordering consistent
        files = files.order_by("name")

    if needs_distinct:
        files = files.distinct()

    if is_org_guest(request.user, org) or is_project_guest(request.user, project):
        files = files.filter(is_kpi_template=False)

    folder_tree = (
        Folder.objects.filter(org=org, project=project, is_archived=False)
        .order_by("path")
    )

    breadcrumb = _breadcrumb_for(folder)

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
        "active_filter": active_filter,
        "active_doc_types": doc_types,
        "upload_url": (
            reverse(
                "drive_upload",
                kwargs={
                    "org_slug": org.slug,
                    "project_slug": project.slug,
                    "folder_id": folder.id,
                },
            )
            if can_edit_drive(request.user, project)
            else None
        ),
        "folder_tree": folder_tree,
        "breadcrumb": breadcrumb,
        "recently_viewed": _recently_viewed_files(request, project),
        "can_edit_drive": can_edit_drive(request.user, project),
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
    project = get_project_for_request(request, org, project_slug)
    forbidden = _require_project_access(request, project)
    if forbidden:
        return forbidden

    folder = get_object_or_404(Folder, pk=folder_id, org=org, project=project)

    def _redirect_back():
        if folder.project_id:
            return redirect(
                "drive_folder",
                org_slug=org.slug,
                project_slug=folder.project.slug,
                folder_id=folder.id,
            )
        return redirect(
            "project_drive_root",
            org_slug=org.slug,
            project_slug=project_slug,
        )

    raw_doc_type = (request.POST.get("doc_type") or "").strip()
    doc_type = raw_doc_type.upper()
    number = (request.POST.get("number") or "").strip()[:128]
    title = (request.POST.get("title") or "").strip()[:256]
    upload = request.FILES.get("file")
    note = (request.POST.get("note") or "").strip()[:500]

    valid_doc_types = {value for value, _label in FileNode.DocType.choices if value}
    valid_doc_types.add("")

    if doc_type and doc_type not in valid_doc_types:
        messages.error(request, "Invalid document type selected.")
        return _redirect_back()

    if doc_type == FileNode.DocType.MTR and not folder.project_id:
        messages.error(request, "MTRs must be uploaded into a project folder.")
        return _redirect_back()

    if not upload:
        messages.error(request, "No file provided.")
        return _redirect_back()

    # Validate size + MIME
    ok, content_type, err = validate_upload(upload, settings.ALLOWED_CONTENT_TYPES, settings.MAX_UPLOAD_SIZE_MB)
    if not ok:
        messages.error(request, err)
        return _redirect_back()

    # Keep original user-facing display name
    display_name = upload.name

    # Sanitize stored filename (affects storage key only)
    upload.name = sanitize_filename(upload.name)

    # Derive node slug from display name stem (so doc.pdf & doc.DOCX share)
    stem, _ext = os.path.splitext(display_name)
    node_slug = slugify(stem)

    with transaction.atomic():
        created_node = False
        try:
            node = (
                FileNode.objects.select_for_update()
                .get(org=org, folder=folder, slug=node_slug)
            )

            # --- ENFORCE CHECKOUT (from Step 3) ---
            if node.checked_out_by_id and node.checked_out_by_id != request.user.id:
                messages.error(request, "This file is checked out by another user. You cannot upload a new version.")
                return _redirect_back()

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
                doc_type=doc_type,
                number=number,
                title=title,
            )
            node.save()
            created_node = True
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
        node.content_type = content_type or upload.content_type or node.content_type
        node.latest_version = fv

        node_updates = ["size", "name", "content_type", "latest_version"]

        if not created_node:
            if doc_type and node.doc_type != doc_type:
                node.doc_type = doc_type
                node_updates.append("doc_type")
            if number and node.number != number:
                node.number = number
                node_updates.append("number")
            if title and node.title != title:
                node.title = title
                node_updates.append("title")

        node.save(update_fields=list(dict.fromkeys(node_updates)))

    try:
        # Audit already wired; ensure UPLOAD is logged with the new version ref
        log_file_event(request, node, FileEvent.Action.UPLOAD, fv)
    except Exception:
        pass

    if node.doc_type == FileNode.DocType.MTR:
        messages.info(request, "MTR uploaded — parsing has been queued. Review drafts once parsing completes.")
        drafts_url = reverse("welds:mtr_draft_list", kwargs={"org_slug": org.slug})
        return HttpResponseRedirect(f"{drafts_url}?file={node.id}")

    messages.success(request, f"Uploaded {display_name} as v{next_version}.")
    return _redirect_back()

def _file_detail_context(request, project, node):
    versions = node.versions.all().order_by("-version")

    try:
        log_file_event(request, node, FileEvent.Action.VIEW)
    except Exception:
        pass

    user_role = None
    try:
        membership = Membership.objects.get(org=request.org, user=request.user)
        user_role = membership.role
    except Membership.DoesNotExist:
        user_role = None

    is_admin_or_owner = user_role in (Membership.Role.ADMIN, Membership.Role.OWNER)
    is_member = can_edit_drive(request.user, project)
    is_creator = node.created_by_id == request.user.id

    can_view_activity = bool(is_member or is_creator)

    is_checked_out = bool(node.checked_out_by_id)
    is_checked_out_by_me = node.checked_out_by_id == request.user.id
    can_checkout = is_member and (not is_checked_out or is_checked_out_by_me)
    can_checkin = is_member and is_checked_out_by_me
    can_force_checkin = is_admin_or_owner and is_checked_out

    events_page = None
    if can_view_activity:
        page = int(request.GET.get("apage", "1") or 1)
        qs = node.events.select_related("actor", "version").order_by("-at")
        paginator = Paginator(qs, 10)
        try:
            events_page = paginator.page(page)
        except EmptyPage:
            events_page = paginator.page(paginator.num_pages)

    mtr_pending_count = 0
    mtr_has_unverified = False
    mtr_review_url = ""
    mtr_manual_approve_url = ""
    mtr_review_filtered_url = ""
    mtr_status_url = ""
    if node.doc_type == FileNode.DocType.MTR:
        mtr_pending_count = node.material_heat_drafts.filter(verified=False).count()
        mtr_has_unverified = mtr_pending_count > 0
        try:
            mtr_review_url = reverse("welds:mtr_draft_list", kwargs={"org_slug": request.org.slug})
        except Exception:
            mtr_review_url = ""
        else:
            separator = "&" if "?" in mtr_review_url else "?"
            mtr_review_filtered_url = f"{mtr_review_url}{separator}file={node.id}"
        try:
            mtr_manual_approve_url = reverse(
                "drive_mtr_manual_approve",
                kwargs={
                    "org_slug": request.org.slug,
                    "project_slug": project.slug,
                    "file_id": node.id,
                },
            )
        except Exception:
            mtr_manual_approve_url = ""
        try:
            mtr_status_url = reverse(
                "drive_mtr_status",
                kwargs={
                    "org_slug": request.org.slug,
                    "project_slug": project.slug,
                    "file_id": node.id,
                },
            )
        except Exception:
            mtr_status_url = ""

    return {
        "org": request.org,
        "project": project,
        "node": node,
        "versions": versions,
        "events_page": events_page,
        "can_view_activity": can_view_activity,
        "is_admin_or_owner": is_admin_or_owner,
        "is_member": is_member,
        "is_creator": is_creator,
        "is_checked_out": is_checked_out,
        "is_checked_out_by_me": is_checked_out_by_me,
        "can_checkout": can_checkout,
        "can_checkin": can_checkin,
        "can_force_checkin": can_force_checkin,
        "mtr_has_unverified_drafts": mtr_has_unverified,
        "mtr_pending_drafts_count": mtr_pending_count,
        "mtr_review_url": mtr_review_url,
        "mtr_review_filtered_url": mtr_review_filtered_url,
        "mtr_manual_approve_url": mtr_manual_approve_url,
        "mtr_can_manual_approve": is_admin_or_owner,
        "mtr_status_url": mtr_status_url,
    }


@login_required
@require_membership("GUEST")
def file_detail(request, org_slug, project_slug, file_id):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_access(request, project)
    if forbidden:
        return forbidden

    node = get_object_or_404(
        FileNode.objects.select_related(
            "latest_version",
            "folder",
            "project",
            "org",
            "created_by",
        ),
        pk=file_id,
        org=request.org,
        project=project,
    )

    blocked = _block_kpi_template(request, project, node)
    if blocked:
        return blocked

    context = _file_detail_context(request, project, node)
    context["standalone"] = True
    return render(request, "drive/file_detail.html", context)


@login_required
@require_membership("GUEST")
@require_GET
def file_mtr_status(request, org_slug, project_slug, file_id):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_access(request, project)
    if forbidden:
        return forbidden

    node = get_object_or_404(
        FileNode.objects.select_related("project"),
        pk=file_id,
        org=request.org,
        project=project,
    )

    blocked = _block_kpi_template(request, project, node)
    if blocked:
        return blocked

    drafts_count = node.material_heat_drafts.filter(org=request.org, verified=False).count()

    payload = {
        "drafts_count": drafts_count,
        "mtr_approved": bool(node.mtr_approved),
    }
    return JsonResponse(payload)


@login_required
@require_membership("GUEST")
def file_detail_drawer(request, org_slug, project_slug, file_id):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_access(request, project)
    if forbidden:
        return forbidden

    node = get_object_or_404(
        FileNode.objects.select_related(
            "latest_version",
            "folder",
            "project",
            "org",
            "created_by",
        ),
        pk=file_id,
        org=request.org,
        project=project,
    )

    blocked = _block_kpi_template(request, project, node)
    if blocked:
        return blocked

    context = _file_detail_context(request, project, node)

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        response = TemplateResponse(request, "drive/file_detail_drawer.html", context)
        response["Cache-Control"] = "no-store"
        return response

    context["standalone"] = True
    response = render(request, "drive/file_detail.html", context)
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_POST
@require_membership("MEMBER")
def file_manual_approve_mtr(request, org_slug, project_slug, file_id):
    project = get_project_for_request(request, request.org, project_slug)
    forbidden = _require_project_access(request, project)
    if forbidden:
        return forbidden

    node = get_object_or_404(
        FileNode.objects.select_related("latest_version", "project", "org"),
        pk=file_id,
        org=request.org,
        project=project,
    )

    if node.doc_type != FileNode.DocType.MTR:
        return HttpResponseForbidden("Only MTR documents can be approved via this action.")

    try:
        membership = Membership.objects.get(org=request.org, user=request.user)
    except Membership.DoesNotExist:
        return HttpResponseForbidden("You do not have access to approve this MTR.")

    if membership.role not in (Membership.Role.ADMIN, Membership.Role.OWNER):
        return HttpResponseForbidden("Only organization admins may approve MTRs.")

    unverified = node.material_heat_drafts.filter(verified=False).exists()
    node.mtr_approved = True
    node.mtr_approved_by = request.user
    node.mtr_approved_at = timezone.now()
    node.mtr_approved_version = node.latest_version
    node.save(
        update_fields=[
            "mtr_approved",
            "mtr_approved_by",
            "mtr_approved_at",
            "mtr_approved_version",
        ]
    )

    if unverified:
        messages.warning(
            request,
            "MTR approved manually. Unverified drafts remain—please ensure they are reviewed.",
        )
    else:
        messages.success(request, "MTR approved for use.")

    return redirect("drive_file", org_slug=request.org.slug, project_slug=project.slug, file_id=node.id)



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
    blocked = _block_kpi_template(request, project, node)
    if blocked:
        return blocked
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
    blocked = _block_kpi_template(request, project, node)
    if blocked:
        return blocked
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
    blocked = _block_kpi_template(request, project, node)
    if blocked:
        return blocked
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
    blocked = _block_kpi_template(request, project, node)
    if blocked:
        return blocked
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
    if forbidden:
        return forbidden

    node = get_object_or_404(
        FileNode.objects.select_for_update(),
        pk=file_id,
        org=request.org,
        project=project,
    )

    with transaction.atomic():
        node = FileNode.objects.select_for_update().get(pk=node.pk)
        node.checked_out_by = None
        node.checked_out_at = None
        node.save(update_fields=["checked_out_by", "checked_out_at"])
        messages.success(
            request,
            "Force check-in complete. Others can upload new versions.",
        )

    try:
        log_file_event(request, node, FileEvent.Action.FORCE_CHECKIN)
    except Exception:
        pass

    return redirect("drive_file", org_slug=request.org.slug, project_slug=project.slug, file_id=node.id)


@login_required
@require_membership("MEMBER")
@require_POST
def file_revert_version(request, org_slug, project_slug, file_id, version):
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

    revert_source = get_object_or_404(
        FileVersion,
        file_node=node,
        version=version,
    )

    note = (request.POST.get("note") or "").strip()

    with transaction.atomic():
        node = FileNode.objects.select_for_update().get(pk=node.pk)
        latest = node.versions.order_by("-version").first()
        next_version = (latest.version if latest else 0) + 1

        with revert_source.blob.open("rb") as fh:
            content = fh.read()

        content_file = ContentFile(content)
        content_file.name = os.path.basename(revert_source.blob.name)

        new_version = FileVersion.objects.create(
            file_node=node,
            version=next_version,
            blob=content_file,
            size=revert_source.size,
            content_type=revert_source.content_type,
            uploaded_by=request.user,
            note=note or f"Reverted to v{revert_source.version}",
        )

        node.latest_version = new_version
        node.size = new_version.size
        node.save(update_fields=["latest_version", "size"])

    try:
        log_file_event(request, node, FileEvent.Action.REVERT, new_version)
    except Exception:
        pass

    messages.success(
        request,
        f"Created version {new_version.version} from v{revert_source.version}.",
    )

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        context = _file_detail_context(request, project, node)
        response = TemplateResponse(request, "drive/file_detail_drawer.html", context)
        response["Cache-Control"] = "no-store"
        return response

    return redirect("drive_file", org_slug=request.org.slug, project_slug=project.slug, file_id=node.id)
