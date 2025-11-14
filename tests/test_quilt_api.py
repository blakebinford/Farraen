from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from drive.models import FileNode, Folder
from organizations.models import Membership, Organization
from projects.models import Project, ProjectMember
from welds.models import MaterialHeat, MaterialHeatDraft, QuiltQueryLog, Weld, WeldRepair


@pytest.fixture
def user(db):
    model = get_user_model()
    return model.objects.create_user(username="quilt-user", email="quilt@example.com", password="test-pass")


@pytest.fixture
def org(user):
    organization = Organization.objects.create(name="Farraen QA", slug="farraen-qa", owner=user)
    Membership.objects.create(org=organization, user=user)
    return organization


@pytest.fixture
def project(org, user):
    project = Project.objects.create(org=org, name="Demo Project", slug="demo", created_by=user)
    ProjectMember.objects.create(project=project, user=user)
    return project


@pytest.fixture
def welder(org):
    from welds.models import Welder

    return Welder.objects.create(org=org, name="Alex Welder", stencil="A-1")


@pytest.fixture
def folder(org):
    return Folder.objects.create(org=org, name="Root", slug="root", path="/root")


@pytest.fixture
def mtr_file(org, project, folder):
    return FileNode.objects.create(org=org, project=project, folder=folder, name="mtr.pdf", slug="mtr")


@pytest.fixture(autouse=True)
def clear_logs():
    QuiltQueryLog.objects.all().delete()
    yield
    QuiltQueryLog.objects.all().delete()


def _create_weld(project, welder, idx: int, with_repair: bool = True, repair_date: date | None = None) -> Weld:
    weld = Weld.objects.create(
        project=project,
        primary_welder=welder,
        weld_id=f"W-{idx}",
        weld_date=date.today() - timedelta(days=idx),
    )
    if with_repair:
        WeldRepair.objects.create(
            weld=weld,
            flagged_at=repair_date or date.today() - timedelta(days=idx),
            repair_date=repair_date or date.today() - timedelta(days=idx),
        )
    return weld


@pytest.mark.django_db
def test_quilt_kpi_and_repair_rate(client, user, org, project, welder):
    # Create 3 repaired welds and 1 clean weld.
    for idx in range(3):
        _create_weld(project, welder, idx)
    _create_weld(project, welder, 99, with_repair=False)

    client.force_login(user)
    response = client.post(
        reverse("welds-api:quilt-query"),
        data=json.dumps({"project_id": project.id}),
        content_type="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["kpi"]["repaired_welds"] == 3
    assert payload["kpi"]["total_welds"] == 4
    assert pytest.approx(payload["kpi"]["repair_rate"], rel=1e-3) == 0.75


@pytest.mark.django_db
def test_quilt_per_page_coercion(client, user, org, project, welder):
    for idx in range(60):
        _create_weld(project, welder, idx)
    client.force_login(user)
    response = client.post(
        reverse("welds-api:quilt-query"),
        data=json.dumps({"project_id": project.id, "per_page": 200}),
        content_type="application/json",
    )
    assert response.status_code == 200
    meta = response.json()["meta"]
    assert meta["per_page"] == 50
    assert meta.get("per_page_capped") is True


@pytest.mark.django_db
def test_quilt_permission_denied(client, user, project, welder):
    outsider = get_user_model().objects.create_user(username="outsider", password="pw")
    client.force_login(outsider)
    response = client.post(
        reverse("welds-api:quilt-query"),
        data=json.dumps({"project_id": project.id}),
        content_type="application/json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_quilt_export_cap(client, user, org, project, welder):
    for idx in range(140):
        _create_weld(project, welder, idx)
    client.force_login(user)
    response = client.post(
        reverse("welds-api:quilt-query"),
        data=json.dumps({"project_id": project.id, "intent": "export_csv", "per_page": 200}),
        content_type="application/json",
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"].get("export_limited") is True
    assert len(payload["welds"]) == 100


@pytest.mark.django_db
def test_quilt_parser_confidence(client, user, org, project, welder, mtr_file):
    heat = MaterialHeat.objects.create(org=org, heat_number="HX-1", mtr_document=mtr_file)
    MaterialHeatDraft.objects.create(
        file_node=mtr_file,
        org=org,
        confidence=0.92,
        raw_payload={"confidence": 0.92, "notes": "from parser"},
    )
    weld = _create_weld(project, welder, 1)
    weld.material1_heat = heat
    weld.save(update_fields=["material1_heat"])

    client.force_login(user)
    response = client.post(
        reverse("welds-api:quilt-query"),
        data=json.dumps({"project_id": project.id, "filters": {"welder_id": welder.id}}),
        content_type="application/json",
    )
    assert response.status_code == 200
    payload = response.json()
    assert any(
        source.get("parser_confidence") == pytest.approx(0.92, rel=1e-5)
        for source in payload["sources"]
        if source.get("type") == "file"
    ), "Parser confidence should surface for MTR sources (docs/mtr_parsing.md)."
