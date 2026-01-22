from __future__ import annotations

import json
from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from drive.models import FileNode, FileVersion, Folder
from organizations.models import Membership, Organization
from projects.models import Project, ProjectMember
from welds.models import Weld, WeldRepair


@pytest.fixture
def access_setup(db):
    user_model = get_user_model()
    project_a_member_user = user_model.objects.create_user(
        username="project-a-member",
        password="pw",
    )
    project_b_member_user = user_model.objects.create_user(
        username="project-b-member",
        password="pw",
    )
    org_guest_user = user_model.objects.create_user(
        username="org-guest",
        password="pw",
    )
    project_a_guest_user = user_model.objects.create_user(
        username="project-a-guest",
        password="pw",
    )
    outsider_user = user_model.objects.create_user(
        username="outsider",
        password="pw",
    )

    org = Organization.objects.create(
        name="Org A",
        slug="org-a",
        owner=project_a_member_user,
    )
    Membership.objects.create(org=org, user=org_guest_user, role=Membership.Role.GUEST)
    Membership.objects.create(org=org, user=project_a_guest_user, role=Membership.Role.GUEST)
    Membership.objects.create(org=org, user=project_a_member_user, role=Membership.Role.MEMBER)
    Membership.objects.create(org=org, user=project_b_member_user, role=Membership.Role.MEMBER)

    project_a = Project.objects.create(
        org=org,
        name="Project A",
        slug="project-a",
        created_by=project_a_member_user,
    )
    project_b = Project.objects.create(
        org=org,
        name="Project B",
        slug="project-b",
        created_by=project_b_member_user,
    )

    ProjectMember.objects.create(
        project=project_a,
        user=project_a_guest_user,
        role=ProjectMember.Role.GUEST,
    )
    ProjectMember.objects.create(
        project=project_a,
        user=project_a_member_user,
        role=ProjectMember.Role.MEMBER,
    )
    ProjectMember.objects.create(
        project=project_b,
        user=project_b_member_user,
        role=ProjectMember.Role.MEMBER,
    )

    folder_a = Folder.objects.create(
        org=org,
        project=project_a,
        name="Folder A",
        created_by=project_a_member_user,
    )
    folder_b = Folder.objects.create(
        org=org,
        project=project_b,
        name="Folder B",
        created_by=project_b_member_user,
    )

    file_a = FileNode.objects.create(
        org=org,
        project=project_a,
        folder=folder_a,
        name="file-a.txt",
        slug="file-a",
        created_by=project_a_member_user,
    )
    file_b = FileNode.objects.create(
        org=org,
        project=project_b,
        folder=folder_b,
        name="file-b.txt",
        slug="file-b",
        created_by=project_b_member_user,
    )

    def _attach_version(node: FileNode, name: str) -> None:
        upload = SimpleUploadedFile(name, b"content", content_type="text/plain")
        version = FileVersion.objects.create(
            file_node=node,
            version=1,
            blob=upload,
            uploaded_by=project_a_member_user,
        )
        node.latest_version = version
        node.size = version.size
        node.content_type = "text/plain"
        node.save(update_fields=["latest_version", "size", "content_type"])

    _attach_version(file_a, "file-a.txt")
    _attach_version(file_b, "file-b.txt")

    weld = Weld.objects.create(project=project_a, weld_id="W-A1")
    repair = WeldRepair.objects.create(weld=weld, flagged_at=date.today())

    return {
        "org": org,
        "project_a": project_a,
        "project_b": project_b,
        "folder_a": folder_a,
        "folder_b": folder_b,
        "file_a": file_a,
        "file_b": file_b,
        "weld": weld,
        "repair": repair,
        "users": {
            "outsider_user": outsider_user,
            "org_guest_user": org_guest_user,
            "project_a_guest_user": project_a_guest_user,
            "project_a_member_user": project_a_member_user,
            "project_b_member_user": project_b_member_user,
        },
    }


def _assert_forbidden(response):
    assert response.status_code in {403, 404}


def _upload_payload():
    return {
        "doc_type": "",
        "number": "",
        "title": "",
        "file": SimpleUploadedFile("upload.txt", b"hello", content_type="text/plain"),
    }


@pytest.mark.django_db
def test_org_boundary_access(access_setup, client):
    org = access_setup["org"]
    outsider = access_setup["users"]["outsider_user"]
    org_guest = access_setup["users"]["org_guest_user"]

    client.force_login(outsider)
    response = client.get(reverse("org_dashboard", kwargs={"org_slug": org.slug}))
    # Regression guard: outsiders should never access org dashboards.
    _assert_forbidden(response)

    response = client.get(reverse("org_members", kwargs={"org_slug": org.slug}))
    # Regression guard: outsiders should not access org-scoped member lists.
    _assert_forbidden(response)

    client.force_login(org_guest)
    response = client.get(reverse("org_dashboard", kwargs={"org_slug": org.slug}))
    # Regression guard: org members must retain dashboard access.
    assert response.status_code == 200


@pytest.mark.django_db
def test_project_membership_required_for_project_pages(access_setup, client):
    org = access_setup["org"]
    project_a = access_setup["project_a"]
    folder_a = access_setup["folder_a"]
    file_a = access_setup["file_a"]
    org_guest = access_setup["users"]["org_guest_user"]

    client.force_login(org_guest)
    response = client.get(
        reverse(
            "projects:project_dashboard",
            kwargs={"org_slug": org.slug, "project_slug": project_a.slug},
        )
    )
    # Regression guard: org-only members cannot access project dashboards.
    _assert_forbidden(response)

    response = client.get(
        reverse(
            "project_drive_root",
            kwargs={"org_slug": org.slug, "project_slug": project_a.slug},
        )
    )
    # Regression guard: org-only members cannot access project drive roots.
    _assert_forbidden(response)

    response = client.get(
        reverse(
            "drive_folder",
            kwargs={
                "org_slug": org.slug,
                "project_slug": project_a.slug,
                "folder_id": folder_a.id,
            },
        )
    )
    # Regression guard: org-only members cannot browse project folders.
    _assert_forbidden(response)

    response = client.get(
        reverse(
            "drive_file",
            kwargs={
                "org_slug": org.slug,
                "project_slug": project_a.slug,
                "file_id": file_a.id,
            },
        )
    )
    # Regression guard: org-only members cannot access project files.
    _assert_forbidden(response)


@pytest.mark.django_db
def test_cross_project_id_guessing_is_blocked(access_setup, client):
    org = access_setup["org"]
    project_a = access_setup["project_a"]
    folder_b = access_setup["folder_b"]
    file_b = access_setup["file_b"]
    member_a = access_setup["users"]["project_a_member_user"]

    client.force_login(member_a)
    response = client.get(
        reverse(
            "drive_folder",
            kwargs={
                "org_slug": org.slug,
                "project_slug": project_a.slug,
                "folder_id": folder_b.id,
            },
        )
    )
    # Regression guard: folder IDs from other projects cannot be accessed.
    _assert_forbidden(response)

    response = client.get(
        reverse(
            "drive_file",
            kwargs={
                "org_slug": org.slug,
                "project_slug": project_a.slug,
                "file_id": file_b.id,
            },
        )
    )
    # Regression guard: file IDs from other projects cannot be accessed.
    _assert_forbidden(response)

    response = client.get(
        reverse(
            "drive_file_download",
            kwargs={
                "org_slug": org.slug,
                "project_slug": project_a.slug,
                "file_id": file_b.id,
            },
        )
    )
    # Regression guard: file downloads must stay within the requested project.
    _assert_forbidden(response)

    response = client.get(
        reverse(
            "drive_file_stream",
            kwargs={
                "org_slug": org.slug,
                "project_slug": project_a.slug,
                "file_id": file_b.id,
            },
        )
    )
    # Regression guard: file previews must stay within the requested project.
    _assert_forbidden(response)


@pytest.mark.django_db
def test_file_upload_hardening(access_setup, client):
    org = access_setup["org"]
    project_a = access_setup["project_a"]
    folder_a = access_setup["folder_a"]
    folder_b = access_setup["folder_b"]
    member_a = access_setup["users"]["project_a_member_user"]
    guest_a = access_setup["users"]["project_a_guest_user"]

    client.force_login(member_a)
    initial_count = FileNode.objects.filter(folder=folder_a).count()
    response = client.post(
        reverse(
            "drive_upload",
            kwargs={
                "org_slug": org.slug,
                "project_slug": project_a.slug,
                "folder_id": folder_a.id,
            },
        ),
        data=_upload_payload(),
    )
    # Regression guard: members can upload into their project folders.
    assert response.status_code in {302, 303}
    assert FileNode.objects.filter(folder=folder_a).count() == initial_count + 1

    response = client.post(
        reverse(
            "drive_upload",
            kwargs={
                "org_slug": org.slug,
                "project_slug": project_a.slug,
                "folder_id": folder_b.id,
            },
        ),
        data=_upload_payload(),
    )
    # Regression guard: folder IDs from other projects cannot be used for uploads.
    _assert_forbidden(response)

    client.force_login(guest_a)
    response = client.post(
        reverse(
            "drive_upload",
            kwargs={
                "org_slug": org.slug,
                "project_slug": project_a.slug,
                "folder_id": folder_a.id,
            },
        ),
        data=_upload_payload(),
    )
    # Regression guard: org guests must not upload files.
    _assert_forbidden(response)


@pytest.mark.django_db
def test_archive_lock_blocks_mutations(access_setup, client):
    org = access_setup["org"]
    project_a = access_setup["project_a"]
    folder_a = access_setup["folder_a"]
    weld = access_setup["weld"]
    repair = access_setup["repair"]
    member_a = access_setup["users"]["project_a_member_user"]

    project_a.status = Project.Status.ARCHIVED
    project_a.save(update_fields=["status"])

    client.force_login(member_a)
    response = client.post(
        reverse(
            "drive_upload",
            kwargs={
                "org_slug": org.slug,
                "project_slug": project_a.slug,
                "folder_id": folder_a.id,
            },
        ),
        data=_upload_payload(),
    )
    # Regression guard: archived projects must block uploads.
    _assert_forbidden(response)

    response = client.post(
        reverse(
            "welds:mark_weld_for_repair",
            kwargs={"org_slug": org.slug, "weld_id": weld.id},
        ),
        data=json.dumps({}),
        content_type="application/json",
    )
    # Regression guard: archived projects must block repair writes.
    _assert_forbidden(response)

    response = client.patch(
        reverse(
            "welds:update_repair",
            kwargs={"org_slug": org.slug, "repair_id": repair.id},
        ),
        data=json.dumps({"comments": "locked"}),
        content_type="application/json",
    )
    # Regression guard: archived projects must block repair updates.
    _assert_forbidden(response)

    response = client.post(
        reverse(
            "welds:repair_add_attempt",
            kwargs={"org_slug": org.slug, "repair_id": repair.id},
        ),
        data=json.dumps({"performed_at": date.today().isoformat()}),
        content_type="application/json",
    )
    # Regression guard: archived projects must block repair attempt writes.
    _assert_forbidden(response)

    response = client.post(
        reverse(
            "welds:repair_add_reinspection",
            kwargs={"org_slug": org.slug, "repair_id": repair.id},
        ),
        data=json.dumps({"reinspection_date": date.today().isoformat()}),
        content_type="application/json",
    )
    # Regression guard: archived projects must block repair reinspection writes.
    _assert_forbidden(response)

    response = client.post(
        reverse(
            "welds:repair_close",
            kwargs={"org_slug": org.slug, "repair_id": repair.id},
        ),
        data=json.dumps({}),
        content_type="application/json",
    )
    # Regression guard: archived projects must block repair close actions.
    _assert_forbidden(response)


@pytest.mark.django_db
def test_weld_repair_write_roles(access_setup, client):
    org = access_setup["org"]
    project_a = access_setup["project_a"]
    guest_a = access_setup["users"]["project_a_guest_user"]
    member_a = access_setup["users"]["project_a_member_user"]

    weld = Weld.objects.create(project=project_a, weld_id="W-A2")
    repair = WeldRepair.objects.create(weld=weld, flagged_at=date.today())

    client.force_login(guest_a)
    response = client.post(
        reverse(
            "welds:mark_weld_for_repair",
            kwargs={"org_slug": org.slug, "weld_id": weld.id},
        ),
        data=json.dumps({}),
        content_type="application/json",
    )
    # Regression guard: org guests cannot create repairs.
    _assert_forbidden(response)

    response = client.patch(
        reverse(
            "welds:update_repair",
            kwargs={"org_slug": org.slug, "repair_id": repair.id},
        ),
        data=json.dumps({"comments": "blocked"}),
        content_type="application/json",
    )
    # Regression guard: org guests cannot update repairs.
    _assert_forbidden(response)

    client.force_login(member_a)
    response = client.post(
        reverse(
            "welds:mark_weld_for_repair",
            kwargs={"org_slug": org.slug, "weld_id": weld.id},
        ),
        data=json.dumps({}),
        content_type="application/json",
    )
    # Regression guard: org members can create repairs.
    assert response.status_code in {200, 201}

    response = client.patch(
        reverse(
            "welds:update_repair",
            kwargs={"org_slug": org.slug, "repair_id": repair.id},
        ),
        data=json.dumps({"comments": "updated"}),
        content_type="application/json",
    )
    # Regression guard: org members can update repairs.
    assert response.status_code == 200

    response = client.post(
        reverse(
            "welds:repair_add_attempt",
            kwargs={"org_slug": org.slug, "repair_id": repair.id},
        ),
        data=json.dumps({"performed_at": date.today().isoformat()}),
        content_type="application/json",
    )
    # Regression guard: org members can add repair attempts.
    assert response.status_code == 201

    response = client.post(
        reverse(
            "welds:repair_add_reinspection",
            kwargs={"org_slug": org.slug, "repair_id": repair.id},
        ),
        data=json.dumps({"reinspection_date": date.today().isoformat()}),
        content_type="application/json",
    )
    # Regression guard: org members can add repair reinspections.
    assert response.status_code == 201

    response = client.post(
        reverse(
            "welds:repair_close",
            kwargs={"org_slug": org.slug, "repair_id": repair.id},
        ),
        data=json.dumps({}),
        content_type="application/json",
    )
    # Regression guard: org members can close repairs.
    assert response.status_code == 200


@pytest.mark.django_db
def test_quilt_role_thresholds(access_setup, client):
    project_a = access_setup["project_a"]
    org = access_setup["org"]
    guest_a = access_setup["users"]["project_a_guest_user"]
    member_a = access_setup["users"]["project_a_member_user"]

    client.force_login(guest_a)
    response = client.post(
        reverse("welds-api:quilt-query"),
        data=json.dumps({"project_id": project_a.id}),
        content_type="application/json",
    )
    # Regression guard: org guests are blocked from Quilt query.
    _assert_forbidden(response)

    response = client.get(reverse("welds-api:quilt-sources"))
    # Regression guard: org guests are blocked from Quilt sources.
    _assert_forbidden(response)

    client.force_login(member_a)
    response = client.post(
        reverse("welds-api:quilt-query"),
        data=json.dumps({"project_id": project_a.id}),
        content_type="application/json",
    )
    # Regression guard: org members can use Quilt query.
    assert response.status_code == 200

    response = client.get(reverse("welds-api:quilt-sources"))
    # Regression guard: org members can access Quilt sources.
    assert response.status_code == 200


@pytest.mark.django_db
def test_kpi_template_hidden_from_guests(access_setup, client):
    org = access_setup["org"]
    project_a = access_setup["project_a"]
    folder_a = access_setup["folder_a"]
    file_a = access_setup["file_a"]
    guest_a = access_setup["users"]["project_a_guest_user"]
    member_a = access_setup["users"]["project_a_member_user"]

    file_a.is_kpi_template = True
    file_a.save(update_fields=["is_kpi_template"])

    client.force_login(guest_a)
    response = client.get(
        reverse(
            "drive_folder",
            kwargs={
                "org_slug": org.slug,
                "project_slug": project_a.slug,
                "folder_id": folder_a.id,
            },
        )
    )
    assert response.status_code == 200
    assert file_a.name not in response.content.decode()

    response = client.get(
        reverse(
            "drive_file",
            kwargs={
                "org_slug": org.slug,
                "project_slug": project_a.slug,
                "file_id": file_a.id,
            },
        )
    )
    _assert_forbidden(response)

    client.force_login(member_a)
    response = client.get(
        reverse(
            "drive_folder",
            kwargs={
                "org_slug": org.slug,
                "project_slug": project_a.slug,
                "folder_id": folder_a.id,
            },
        )
    )
    assert response.status_code == 200
    assert file_a.name in response.content.decode()
