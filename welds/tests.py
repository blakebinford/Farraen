from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from organizations.models import Membership, Organization
from projects.models import Project, ProjectMember

from .models import MaterialHeat, NDERig, Weld


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

