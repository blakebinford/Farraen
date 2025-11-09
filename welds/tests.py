from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from organizations.models import Membership, Organization
from projects.models import Project, ProjectMember

from .models import MaterialHeat, NDERig, Weld
from .views import _migrations_required_message


class WeldLogAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            email="user@example.com",
            password="pass1234",
        )
        self.org = Organization.objects.create(
            name="Acme", slug="acme", owner=self.user
        )
        Membership.objects.create(
            org=self.org,
            user=self.user,
            role=Membership.Role.ADMIN,
        )
        self.project = Project.objects.create(
            org=self.org,
            name="Pipeline A",
            slug="pipeline-a",
            created_by=self.user,
            is_archived=False,
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMember.Role.MEMBER,
        )
        self.heat = MaterialHeat.objects.create(
            org=self.org,
            heat_number="H-123",
            description="Pipe",
            material_grade="X52",
            outer_diameter_in=Decimal("10.750"),
            wall_thickness_in=Decimal("0.500"),
            wps_number="WPS-1",
        )
        self.rig = NDERig.objects.create(
            org=self.org,
            name="Gamma Rig",
        )
        self.client.force_login(self.user)

    def _data_url(self, name="weld_log_data"):
        return reverse(
            name,
            kwargs={
                "org_slug": self.org.slug,
                "project_slug": self.project.slug,
            },
        )

    def test_create_weld_applies_material_defaults(self):
        payload = {
            "weld_id": "W-001",
            "material1_heat_id": self.heat.id,
            "material2_heat_id": self.heat.id,
            "nde_rig_id": self.rig.id,
            "disposition": Weld.Disposition.ACCEPTED,
        }
        response = self.client.post(
            self._data_url(),
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()["weld"]
        self.assertEqual(data["material1_description"], self.heat.description)
        self.assertEqual(data["material1_grade"], self.heat.material_grade)
        self.assertEqual(data["material1_outer_diameter_in"], "10.75")
        self.assertEqual(data["nde_rig_name"], self.rig.name)

        weld = Weld.objects.get(pk=data["id"])
        self.assertEqual(weld.material1_heat, self.heat)
        self.assertEqual(weld.material1_description, self.heat.description)
        self.assertEqual(weld.material2_heat, self.heat)
        self.assertEqual(weld.nde_rig, self.rig)

    def test_repair_disposition_requires_comment(self):
        payload = {
            "weld_id": "W-002",
            "disposition": Weld.Disposition.REPAIR,
        }
        response = self.client.post(
            self._data_url(),
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_material_heat_options_endpoint(self):
        url = self._data_url("weld_material_heat_options")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        heats = response.json().get("heats", [])
        self.assertEqual(len(heats), 1)
        self.assertEqual(heats[0]["heat_number"], self.heat.heat_number)

    def test_material_heat_search_endpoint(self):
        url = self._data_url("weld_material_heat_search")
        response = self.client.get(url, {"q": "H-1"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        results = payload.get("results", [])
        self.assertEqual(len(results), 1)
        match = results[0]
        self.assertEqual(match["heat_number"], self.heat.heat_number)
        self.assertEqual(match["description"], self.heat.description)
        self.assertEqual(match["grade"], self.heat.material_grade)
        self.assertEqual(match["od"], "10.75")

    def test_material_heat_search_requires_two_characters(self):
        url = self._data_url("weld_material_heat_search")
        response = self.client.get(url, {"q": "H"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"results": []})

    def test_weld_log_data_includes_row_count(self):
        Weld.objects.create(
            project=self.project,
            weld_id="W-ROW-1",
            created_by=self.user,
            updated_by=self.user,
        )
        response = self.client.get(self._data_url())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("row_count", payload)
        self.assertEqual(payload["row_count"], 1)
        self.assertEqual(len(payload.get("rows", [])), 1)


class WeldLogMissingTablesTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            email="viewer@example.com",
            password="pass1234",
        )
        self.org = Organization.objects.create(
            name="Acme", slug="acme", owner=self.user
        )
        Membership.objects.create(
            org=self.org,
            user=self.user,
            role=Membership.Role.ADMIN,
        )
        self.project = Project.objects.create(
            org=self.org,
            name="Pipeline A",
            slug="pipeline-a",
            created_by=self.user,
            is_archived=False,
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMember.Role.MEMBER,
        )
        self.client.force_login(self.user)

    def _weld_log_url(self, name="weld_log"):
        return reverse(
            name,
            kwargs={
                "org_slug": self.org.slug,
                "project_slug": self.project.slug,
            },
        )

    @patch("welds.views._missing_weld_tables", return_value=["weld log entries"])
    def test_weld_log_page_prompts_for_migration(self, mock_missing):
        response = self.client.get(self._weld_log_url())
        self.assertEqual(response.status_code, 503)
        expected_message = _migrations_required_message(["weld log entries"])
        self.assertEqual(response.context["setup_error"], expected_message)
        self.assertContains(response, expected_message, status_code=503)
        mock_missing.assert_called_once()

    @patch("welds.views._missing_weld_tables", return_value=["weld log entries"])
    def test_weld_log_data_missing_tables_returns_503(self, mock_missing):
        response = self.client.get(self._weld_log_url("weld_log_data"))
        self.assertEqual(response.status_code, 503)
        expected_message = _migrations_required_message(["weld log entries"])
        self.assertEqual(response.json(), {"error": expected_message})
        mock_missing.assert_called_once()

    @patch("welds.views._missing_weld_tables", return_value=["NDE rigs"])
    def test_option_endpoints_missing_tables_return_503(self, mock_missing):
        response = self.client.get(self._weld_log_url("weld_material_heat_options"))
        self.assertEqual(response.status_code, 503)
        expected_message = _migrations_required_message(["NDE rigs"])
        self.assertEqual(response.json(), {"error": expected_message})

        response = self.client.get(self._weld_log_url("weld_material_heat_search"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": expected_message})

        response = self.client.get(self._weld_log_url("weld_nde_rig_options"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": expected_message})
        self.assertEqual(mock_missing.call_count, 3)

