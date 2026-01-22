import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from organizations.models import Membership, Organization
from projects.models import Project, ProjectMember
from drive.models import Folder
from welds.models import Weld


@pytest.mark.django_db
def test_project_role_permissions_for_drive_and_welds(client):
    user_model = get_user_model()
    owner = user_model.objects.create_user(username="owner", password="pw")
    member_user = user_model.objects.create_user(username="member", password="pw")
    tech_user = user_model.objects.create_user(username="tech", password="pw")
    viewer_user = user_model.objects.create_user(username="viewer", password="pw")
    guest_user = user_model.objects.create_user(username="guest", password="pw")

    org = Organization.objects.create(name="Org", slug="org", owner=owner)
    Membership.objects.create(org=org, user=owner, role=Membership.Role.OWNER)
    Membership.objects.create(org=org, user=member_user, role=Membership.Role.MEMBER)
    Membership.objects.create(org=org, user=tech_user, role=Membership.Role.MEMBER)
    Membership.objects.create(org=org, user=viewer_user, role=Membership.Role.MEMBER)
    Membership.objects.create(org=org, user=guest_user, role=Membership.Role.GUEST)

    project = Project.objects.create(org=org, name="Project", slug="project", created_by=owner)
    folder = Folder.objects.create(org=org, project=project, name="Folder", created_by=owner)

    ProjectMember.objects.create(project=project, user=member_user, role=ProjectMember.Role.MEMBER)
    ProjectMember.objects.create(project=project, user=tech_user, role=ProjectMember.Role.QUALITY_TECH)
    ProjectMember.objects.create(project=project, user=viewer_user, role=ProjectMember.Role.VIEWER)
    ProjectMember.objects.create(project=project, user=guest_user, role=ProjectMember.Role.GUEST)

    weld = Weld.objects.create(project=project, weld_id="W-1")

    upload_url = reverse(
        "drive_upload",
        kwargs={"org_slug": org.slug, "project_slug": project.slug, "folder_id": folder.id},
    )
    payload = {
        "doc_type": "",
        "number": "",
        "title": "",
        "file": SimpleUploadedFile("upload.txt", b"hello", content_type="text/plain"),
    }

    client.force_login(member_user)
    response = client.post(upload_url, data=payload)
    assert response.status_code in {302, 303}

    client.force_login(tech_user)
    response = client.post(upload_url, data=payload)
    assert response.status_code in {302, 303}

    client.force_login(viewer_user)
    response = client.post(upload_url, data=payload)
    assert response.status_code in {403, 404}

    client.force_login(guest_user)
    response = client.post(upload_url, data=payload)
    assert response.status_code in {403, 404}

    repair_url = reverse("welds:mark_weld_for_repair", kwargs={"org_slug": org.slug, "weld_id": weld.id})

    client.force_login(member_user)
    response = client.post(repair_url, data="{}", content_type="application/json")
    assert response.status_code in {200, 201}

    client.force_login(tech_user)
    response = client.post(repair_url, data="{}", content_type="application/json")
    assert response.status_code in {403, 404}

    client.force_login(viewer_user)
    response = client.post(repair_url, data="{}", content_type="application/json")
    assert response.status_code in {403, 404}

    client.force_login(guest_user)
    response = client.post(repair_url, data="{}", content_type="application/json")
    assert response.status_code in {403, 404}
