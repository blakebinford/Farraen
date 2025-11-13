import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from drive.models import FileNode, Folder
from organizations.models import Membership, Organization
from projects.models import Project, ProjectMember

from .analytics import build_dashboard_analytics, build_drilldown
from .models import (
    MaterialHeat,
    NominalPipeOD,
    NDERig,
    RepairAttempt,
    Reinspection,
    Welder,
    Weld,
    WeldEvent,
    WeldHistory,
    WeldInspection,
    WeldRepair,
)
from .services import build_weld_dashboard_chart_payload, get_project_weld_kpis
from .views import _migrations_required_message


class WelderModelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            email="welder@example.com",
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
            status=Project.Status.ACTIVE,
        )

    def test_welder_can_link_wps_documents(self):
        welder = Welder.objects.create(
            org=self.org,
            name="Alice Welder",
            stencil="A123",
        )
        wps_folder = Folder.objects.get(
            org=self.org, project=self.project, name="WPS"
        )
        wps = FileNode.objects.create(
            org=self.org,
            project=self.project,
            folder=wps_folder,
            name="WPS-001.pdf",
            doc_type=FileNode.DocType.WPS,
        )

        welder.approved_wps.add(wps)

        self.assertIn(wps, welder.approved_wps.all())


class NDERigModelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            email="nde@example.com",
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
            status=Project.Status.ACTIVE,
        )

    def test_qualification_folder_created(self):
        rig = NDERig.objects.create(
            org=self.org,
            project=self.project,
            name="Gamma Rig",
        )
        rig.refresh_from_db()
        self.assertIsNotNone(rig.qualification_folder)
        self.assertEqual(rig.qualification_folder.name, rig.name)
        self.assertEqual(
            rig.qualification_folder.parent.name,
            "Inspector Qualification",
        )


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
            status=Project.Status.ACTIVE,
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
            project=self.project,
            name="Gamma Rig",
        )
        self.rig.refresh_from_db()
        self.welder = Welder.objects.create(
            org=self.org,
            name="Alice Welder",
            stencil="A123",
        )
        self.client.force_login(self.user)

    def _data_url(self, name="weld_log_data"):
        return reverse(
            f"welds:{name}",
            kwargs={
                "org_slug": self.org.slug,
                "project_slug": self.project.slug,
            },
        )

    def _history_data_url(self):
        return reverse(
            "welds:weld_history_data",
            kwargs={
                "org_slug": self.org.slug,
                "project_slug": self.project.slug,
            },
        )

    def _history_page_url(self):
        return reverse(
            "welds:weld_history_page",
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
            "welder_stencil_root_hotpass": self.welder.stencil,
            "welder_stencil_fill": f"{self.welder.stencil}, B456",
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
        self.assertEqual(
            data["welder_stencil_root_hotpass"], self.welder.stencil
        )
        self.assertEqual(
            data["welder_stencil_fill"], f"{self.welder.stencil}, B456"
        )
        self.assertEqual(data["welder_stencil_cap"], "")
        self.assertEqual(data["welder_stencil_repair"], "")
        self.assertTrue(data["nde_rig_folder_url"])

        weld = Weld.objects.get(pk=data["id"])
        self.assertEqual(weld.material1_heat, self.heat)
        self.assertEqual(weld.material1_description, self.heat.description)
        self.assertEqual(weld.material2_heat, self.heat)
        self.assertEqual(weld.nde_rig, self.rig)
        self.assertEqual(weld.welder_stencil_root_hotpass, self.welder.stencil)
        self.assertEqual(
            weld.welder_stencil_fill, f"{self.welder.stencil}, B456"
        )

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

    def test_create_weld_defaults_to_pending(self):
        payload = {"weld_id": "W-003"}
        response = self.client.post(
            self._data_url(),
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        result = response.json()["weld"]
        self.assertEqual(result["disposition"], Weld.Disposition.PENDING)
        self.assertEqual(result["disposition_label"], "Pending")
        self.assertIsNone(result["repair_type"])
        self.assertIsNone(result["weld_type"])

    def test_material_heat_options_endpoint(self):
        url = self._data_url("weld_material_heat_options")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        heats = response.json().get("heats", [])
        self.assertEqual(len(heats), 1)
        self.assertEqual(heats[0]["heat_number"], self.heat.heat_number)

    def test_welder_options_endpoint(self):
        url = self._data_url("weld_welder_options")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        welders = response.json().get("welders", [])
        self.assertEqual(len(welders), 1)
        self.assertEqual(welders[0]["stencil"], self.welder.stencil)

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

    def test_nde_rig_options_include_folder_link(self):
        url = self._data_url("weld_nde_rig_options")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        rigs = response.json().get("nde_rigs", [])
        self.assertEqual(len(rigs), 1)
        rig = rigs[0]
        expected_url = reverse(
            "drive_folder",
            kwargs={
                "org_slug": self.org.slug,
                "project_slug": self.project.slug,
                "folder_id": self.rig.qualification_folder_id,
            },
        )
        self.assertEqual(rig["qualification_folder_url"], expected_url)

    def test_material_heat_search_requires_two_characters(self):
        url = self._data_url("weld_material_heat_search")
        response = self.client.get(url, {"q": "H"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"results": []})

    def test_weld_log_data_includes_row_count(self):
        Weld.objects.create(
            project=self.project,
            weld_id="W-ROW-1",
            welder_stencil_root_hotpass=self.welder.stencil,
            nde_rig=self.rig,
            created_by=self.user,
            updated_by=self.user,
        )
        response = self.client.get(self._data_url())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("row_count", payload)
        self.assertEqual(payload["row_count"], 1)
        self.assertEqual(len(payload.get("rows", [])), 1)
        row = payload["rows"][0]
        self.assertEqual(row["welder_stencil_root_hotpass"], self.welder.stencil)
        self.assertEqual(row.get("welder_stencil_repair"), "")
        self.assertEqual(row["nde_rig_id"], self.rig.id)
        self.assertTrue(row["nde_rig_folder_url"])


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
            status=Project.Status.ACTIVE,
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMember.Role.MEMBER,
        )
        self.client.force_login(self.user)

    def _weld_log_url(self, name="weld_log"):
        return reverse(
            f"welds:{name}",
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

        response = self.client.get(self._weld_log_url("weld_welder_options"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": expected_message})

        self.assertEqual(mock_missing.call_count, 4)


class WeldKPIDashboardServiceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            email="kpi@example.com",
            password="pass1234",
        )
        self.org = Organization.objects.create(
            name="MetricsCo",
            slug="metricsco",
            owner=self.user,
        )
        Membership.objects.create(
            org=self.org,
            user=self.user,
            role=Membership.Role.ADMIN,
        )
        self.project = Project.objects.create(
            org=self.org,
            name="Pipeline B",
            slug="pipeline-b",
            created_by=self.user,
            status=Project.Status.ACTIVE,
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMember.Role.MEMBER,
        )
        self.rig = NDERig.objects.create(
            org=self.org,
            project=self.project,
            name="Gamma Rig",
        )
        self.heat_primary = MaterialHeat.objects.create(
            org=self.org,
            heat_number="H-PRIMARY",
            description="12 in pipe",
            material_grade="X60",
            outer_diameter_in=Decimal("12.000"),
            wps_number="WPS-100",
        )
        self.heat_secondary = MaterialHeat.objects.create(
            org=self.org,
            heat_number="H-SECONDARY",
            description="8 in pipe",
            material_grade="X52",
            outer_diameter_in=Decimal("8.000"),
            wps_number="WPS-200",
        )
        self.welder_a = Welder.objects.create(
            org=self.org,
            name="Alice Root",
            stencil="A1",
        )
        self.welder_b = Welder.objects.create(
            org=self.org,
            name="Bob Fill",
            stencil="B2",
        )

    def _create_sample_welds(self):
        Weld.objects.create(
            project=self.project,
            weld_id="W-001",
            material1_heat=self.heat_primary,
            material1_outer_diameter_in=Decimal("12.000"),
            date_welded=date(2024, 1, 2),
            nde_type=Weld.NDEType.RADIOGRAPHIC,
            nde_date=date(2024, 1, 5),
            welder_stencil_root_hotpass=self.welder_a.stencil,
            welder_stencil_fill=self.welder_b.stencil,
            welder_stencil_cap=self.welder_a.stencil,
            disposition=Weld.Disposition.ACCEPTED,
        )
        Weld.objects.create(
            project=self.project,
            weld_id="W-002",
            material1_heat=self.heat_secondary,
            date_welded=date(2024, 1, 3),
            nde_type=Weld.NDEType.ULTRASONIC,
            nde_date=date(2024, 1, 6),
            nde_rig=self.rig,
            welder_stencil_root_hotpass=self.welder_b.stencil,
            welder_stencil_repair=self.welder_a.stencil,
            disposition=Weld.Disposition.REPAIR,
            repair_type=Weld.RepairType.POROSITY,
        )

    def test_get_project_weld_kpis_calculates_metrics(self):
        self._create_sample_welds()

        kpis = get_project_weld_kpis(self.project)

        self.assertEqual(kpis["overall"]["total_welds"], 2)
        self.assertEqual(kpis["overall"]["nde_completed"], 2)
        self.assertEqual(kpis["overall"]["accepted"], 1)
        self.assertEqual(kpis["overall"]["repairs"], 1)
        self.assertAlmostEqual(
            float(kpis["overall"]["total_weld_inches"]),
            62.8,
            places=2,
        )
        self.assertAlmostEqual(
            float(kpis["overall"]["average_weld_inches_per_weld"]),
            31.4,
            places=2,
        )
        self.assertAlmostEqual(
            float(kpis["overall"]["project_repair_rate"]),
            50.0,
            places=1,
        )

        self.assertEqual(len(kpis["wps_stats"]), 2)
        wps_labels = {entry["wps_label"] for entry in kpis["wps_stats"]}
        self.assertSetEqual(wps_labels, {"WPS-100", "WPS-200"})
        wps_stats = {entry["wps_label"]: entry for entry in kpis["wps_stats"]}
        self.assertAlmostEqual(
            float(wps_stats["WPS-200"]["repair_share_percent"]), 100.0, places=1
        )
        self.assertAlmostEqual(
            float(wps_stats["WPS-100"]["repair_share_percent"]), 0.0, places=1
        )

        welder_stats = {entry["welder_stencil"]: entry for entry in kpis["welder_stats"]}
        self.assertEqual(welder_stats["A1"]["weld_count"], 2)
        self.assertEqual(welder_stats["A1"]["repair_pass_count"], 1)
        self.assertEqual(welder_stats["B2"]["weld_count"], 2)
        self.assertEqual(welder_stats["B2"].get("repair_pass_count"), 0)

        self.assertEqual(len(kpis["production"]["time_series"]), 2)
        self.assertIsNotNone(kpis["production"]["best_day"])
        self.assertIsNotNone(kpis["production"]["worst_day"])

        self.assertEqual(len(kpis["repair_type_stats"]), 1)
        self.assertEqual(kpis["repair_type_stats"][0]["repair_count"], 1)

        self.assertEqual(len(kpis["repair_nde_rig_stats"]), 1)
        self.assertEqual(kpis["repair_nde_rig_stats"][0]["repair_count"], 1)

    def test_build_chart_payload_is_serializable(self):
        self._create_sample_welds()
        kpis = get_project_weld_kpis(self.project)
        payload = build_weld_dashboard_chart_payload(kpis)

        self.assertIsInstance(payload["repair_rate_by_wps"]["repair_rates"][0], float)
        self.assertIsInstance(payload["repair_rate_by_welder"]["repair_rates"][0], float)
        self.assertEqual(payload["repair_rate_by_wps"]["total_repairs"], 1)
        self.assertIn(1, payload["repair_rate_by_welder"]["repair_counts"])
        self.assertIn("POROSITY", payload["repairs_by_type"]["labels"][0].upper())
        self.assertIn(1, payload["repairs_by_type"]["repair_counts"])
        self.assertRegex(payload["weld_inches_by_day"]["labels"][0], r"\d{4}-\d{2}-\d{2}")


class WeldHistoryAPITests(WeldLogAPITests):
    def _post_weld(self, payload):
        return self.client.post(
            self._data_url(), data=payload, content_type="application/json"
        )

    def _base_payload(self, **overrides):
        data = {
            "weld_id": overrides.pop("weld_id", "W-001"),
            "disposition": overrides.pop(
                "disposition", Weld.Disposition.ACCEPTED
            ),
        }
        data.update(overrides)
        return data

    def test_history_created_on_create(self):
        response = self._post_weld(self._base_payload())
        self.assertEqual(response.status_code, 201)
        weld = Weld.objects.get(pk=response.json()["weld"]["id"])

        entries = list(weld.history.all())
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.change_type, WeldHistory.ChangeType.CREATE)
        self.assertEqual(entry.changed_by, self.user)
        self.assertEqual(entry.before, {})
        self.assertEqual(entry.after.get("weld_id"), "W-001")
        self.assertIn("weld_id", entry.changed_fields)
        self.assertEqual(entry.reason, "")

    def test_history_created_on_update(self):
        create = self._post_weld(self._base_payload())
        self.assertEqual(create.status_code, 201)
        weld_id = create.json()["weld"]["id"]
        payload = self._base_payload(nde_number="NDE-100")
        payload["id"] = weld_id
        update = self._post_weld(payload)
        self.assertEqual(update.status_code, 200)

        weld = Weld.objects.get(pk=weld_id)
        history_entries = list(
            weld.history.order_by("created_at", "id")
        )
        self.assertEqual(len(history_entries), 2)
        update_entry = history_entries[-1]
        self.assertEqual(update_entry.change_type, WeldHistory.ChangeType.UPDATE)
        self.assertIn("nde_number", update_entry.changed_fields)
        self.assertEqual(update_entry.before.get("nde_number"), "")
        self.assertEqual(update_entry.after.get("nde_number"), "NDE-100")

    def test_history_endpoint_returns_entries_sorted(self):
        create = self._post_weld(self._base_payload())
        self.assertEqual(create.status_code, 201)
        weld_id = create.json()["weld"]["id"]
        payload = self._base_payload(nde_number="A1")
        payload["id"] = weld_id
        self.assertEqual(self._post_weld(payload).status_code, 200)

        weld = Weld.objects.get(pk=weld_id)
        url = reverse(
            "welds:weld_history",
            kwargs={
                "org_slug": self.org.slug,
                "project_slug": self.project.slug,
                "weld_pk": weld.id,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        rows = response.json().get("rows", [])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["change_type"], "UPDATE")
        self.assertEqual(rows[1]["change_type"], "CREATE")
        self.assertEqual(rows[0]["after"].get("nde_number"), "A1")
        self.assertEqual(rows[0]["changed_by"]["display_name"], self.user.email)

    def test_rollback_requires_manager_permission(self):
        create = self._post_weld(self._base_payload())
        self.assertEqual(create.status_code, 201)
        weld_id = create.json()["weld"]["id"]
        payload = self._base_payload(nde_number="PRE")
        payload["id"] = weld_id
        self.assertEqual(self._post_weld(payload).status_code, 200)
        weld = Weld.objects.get(pk=weld_id)
        history_entry = weld.history.filter(
            change_type=WeldHistory.ChangeType.UPDATE
        ).first()
        rollback_url = reverse(
            "welds:weld_history_rollback",
            kwargs={
                "org_slug": self.org.slug,
                "project_slug": self.project.slug,
                "history_id": history_entry.id,
            },
        )
        response = self.client.post(
            rollback_url, data={}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 403)

    def test_rollback_restores_previous_values_and_creates_history(self):
        ProjectMember.objects.filter(
            project=self.project, user=self.user
        ).update(role=ProjectMember.Role.PROJECT_MANAGER)
        create = self._post_weld(self._base_payload())
        self.assertEqual(create.status_code, 201)
        weld_id = create.json()["weld"]["id"]
        payload = self._base_payload(nde_number="PRE")
        payload["id"] = weld_id
        self.assertEqual(self._post_weld(payload).status_code, 200)
        weld = Weld.objects.get(pk=weld_id)
        update_entry = weld.history.filter(
            change_type=WeldHistory.ChangeType.UPDATE
        ).first()
        rollback_url = reverse(
            "welds:weld_history_rollback",
            kwargs={
                "org_slug": self.org.slug,
                "project_slug": self.project.slug,
                "history_id": update_entry.id,
            },
        )
        response = self.client.post(
            rollback_url,
            data=json.dumps({"reason": "Revert test"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["weld"]["nde_number"], "")

        weld.refresh_from_db()
        history_entries = list(
            weld.history.order_by("-created_at", "-id")
        )
        self.assertEqual(len(history_entries), 3)
        rollback_entry = history_entries[0]
        self.assertEqual(
            rollback_entry.change_type, WeldHistory.ChangeType.ROLLBACK
        )
        self.assertEqual(rollback_entry.reason, "Revert test")
        self.assertEqual(rollback_entry.before.get("nde_number"), "PRE")
        self.assertEqual(rollback_entry.after.get("nde_number"), "")

    def test_rollback_conflict_returns_current_and_target(self):
        ProjectMember.objects.filter(
            project=self.project, user=self.user
        ).update(role=ProjectMember.Role.PROJECT_MANAGER)
        create = self._post_weld(self._base_payload())
        self.assertEqual(create.status_code, 201)
        weld_id = create.json()["weld"]["id"]
        payload = self._base_payload(nde_number="PRE")
        payload["id"] = weld_id
        self.assertEqual(self._post_weld(payload).status_code, 200)
        payload["nde_number"] = "POST"
        self.assertEqual(self._post_weld(payload).status_code, 200)
        weld = Weld.objects.get(pk=weld_id)
        target_entry = weld.history.filter(
            change_type=WeldHistory.ChangeType.UPDATE
        ).order_by("created_at").first()
        rollback_url = reverse(
            "welds:weld_history_rollback",
            kwargs={
                "org_slug": self.org.slug,
                "project_slug": self.project.slug,
                "history_id": target_entry.id,
            },
        )
        response = self.client.post(
            rollback_url,
            data=json.dumps({"reason": "Attempt"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        payload = response.json()
        self.assertEqual(payload["error"], "conflict")
        self.assertEqual(payload["current"].get("nde_number"), "POST")
        self.assertEqual(payload["target"].get("nde_number"), "")

    def test_rollback_records_reason_in_history_response(self):
        ProjectMember.objects.filter(
            project=self.project, user=self.user
        ).update(role=ProjectMember.Role.PROJECT_MANAGER)
        create = self._post_weld(self._base_payload())
        self.assertEqual(create.status_code, 201)
        weld_id = create.json()["weld"]["id"]
        payload = self._base_payload(nde_number="PRE")
        payload["id"] = weld_id
        self.assertEqual(self._post_weld(payload).status_code, 200)
        weld = Weld.objects.get(pk=weld_id)
        update_entry = weld.history.filter(
            change_type=WeldHistory.ChangeType.UPDATE
        ).first()
        rollback_url = reverse(
            "welds:weld_history_rollback",
            kwargs={
                "org_slug": self.org.slug,
                "project_slug": self.project.slug,
                "history_id": update_entry.id,
            },
        )
        reason = "QA requested rollback"
        response = self.client.post(
            rollback_url,
            data=json.dumps({"reason": reason}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        history = response.json().get("history", {})
        self.assertEqual(history.get("reason"), reason)
        url = reverse(
            "welds:weld_history",
            kwargs={
                "org_slug": self.org.slug,
                "project_slug": self.project.slug,
                "weld_pk": weld.id,
            },
        )
        entries = self.client.get(url).json().get("rows", [])
        self.assertEqual(entries[0].get("reason"), reason)


class WeldHistoryDataTests(WeldLogAPITests):
    def test_missing_weld_id_returns_400(self):
        response = self.client.get(self._history_data_url())
        self.assertEqual(response.status_code, 400)
        self.assertIn("weld_id", response.json().get("error", ""))

    def test_unknown_weld_returns_404(self):
        response = self.client.get(
            self._history_data_url(), {"weld_id": "NO-SUCH-WELD"}
        )
        self.assertEqual(response.status_code, 404)

    def test_history_endpoint_returns_weld_and_events(self):
        weld = Weld.objects.create(
            project=self.project,
            weld_id="HX-100",
            created_by=self.user,
            updated_by=self.user,
        )
        WeldEvent.objects.create(
            weld=weld,
            action=WeldEvent.Action.CREATE,
            actor=self.user,
            changes={"weld_id": [None, weld.weld_id]},
            ip_address="127.0.0.1",
        )

        response = self.client.get(
            self._history_data_url(), {"weld_id": weld.weld_id}
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["weld"]["weld_id"], weld.weld_id)
        events = payload.get("events", [])
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["action"], WeldEvent.Action.CREATE)
        self.assertEqual(event["actor_name"], self.user.email)
        self.assertEqual(event["changes"].get("weld_id"), [None, weld.weld_id])


class WeldHistoryPageTests(WeldLogAPITests):
    def test_missing_weld_id_shows_error(self):
        response = self.client.get(self._history_page_url())
        self.assertEqual(response.status_code, 400)
        self.assertIn("Missing weld_id", response.content.decode())

    def test_history_page_renders_weld_information(self):
        payload = {
            "weld_id": "PAGE-001",
            "disposition": Weld.Disposition.ACCEPTED,
        }
        create_response = self.client.post(
            self._data_url(), data=payload, content_type="application/json"
        )
        self.assertEqual(create_response.status_code, 201)

        weld_identifier = payload["weld_id"]
        response = self.client.get(
            self._history_page_url(), {"weld_id": weld_identifier}
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Weld Audit History", content)
        self.assertIn(weld_identifier, content)
        self.assertIn("Audit Trail", content)


class WeldEventAuditTests(WeldLogAPITests):
    def test_create_weld_records_audit_event(self):
        payload = {
            "weld_id": "AUD-001",
            "disposition": Weld.Disposition.ACCEPTED,
        }
        response = self.client.post(
            self._data_url(), data=payload, content_type="application/json"
        )
        self.assertEqual(response.status_code, 201)
        weld_data = response.json()["weld"]
        weld = Weld.objects.get(pk=weld_data["id"])

        events = list(WeldEvent.objects.filter(weld=weld))
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.action, WeldEvent.Action.CREATE)
        self.assertEqual(event.actor, self.user)
        self.assertEqual(event.changes.get("weld_id"), [None, payload["weld_id"]])

    def test_update_weld_records_audit_event(self):
        create_payload = {
            "weld_id": "AUD-200",
            "disposition": Weld.Disposition.PENDING,
        }
        create_response = self.client.post(
            self._data_url(),
            data=create_payload,
            content_type="application/json",
        )
        self.assertEqual(create_response.status_code, 201)
        weld_data = create_response.json()["weld"]

        update_payload = {
            "id": weld_data["id"],
            "weld_id": weld_data["weld_id"],
            "disposition": Weld.Disposition.ACCEPTED,
            "disposition_comment": "Inspector approved",
        }
        update_response = self.client.post(
            self._data_url(),
            data=update_payload,
            content_type="application/json",
        )
        self.assertEqual(update_response.status_code, 200)

        weld = Weld.objects.get(pk=weld_data["id"])
        events = list(WeldEvent.objects.filter(weld=weld).order_by("created_at"))
        self.assertEqual(len(events), 2)
        update_event = events[-1]
        self.assertEqual(update_event.action, WeldEvent.Action.UPDATE)
        self.assertEqual(update_event.actor, self.user)
        self.assertEqual(
            update_event.changes.get("disposition"),
            [Weld.Disposition.PENDING, Weld.Disposition.ACCEPTED],
        )
        self.assertEqual(
            update_event.changes.get("disposition_comment"),
            ["", "Inspector approved"],
        )



class DashboardAnalyticsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            email="analytics@example.com",
            password="pass1234",
        )
        self.org = Organization.objects.create(
            name="AnalyticsOrg",
            slug="analytics-org",
            owner=self.user,
        )
        Membership.objects.create(
            org=self.org,
            user=self.user,
            role=Membership.Role.ADMIN,
        )
        self.project = Project.objects.create(
            org=self.org,
            name="Pipeline Metrics",
            slug="pipeline-metrics",
            created_by=self.user,
            status=Project.Status.ACTIVE,
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMember.Role.MEMBER,
        )
        self.welder = Welder.objects.create(
            org=self.org,
            name="Wendy Welder",
            stencil="WW1",
        )
        self.wps_folder = Folder.objects.get(
            org=self.org,
            project=self.project,
            name="WPS",
        )
        self.wps_document = FileNode.objects.create(
            org=self.org,
            project=self.project,
            folder=self.wps_folder,
            name="WPS-ANALYTICS-001.pdf",
            doc_type=FileNode.DocType.WPS,
        )
        self.nde_rig = NDERig.objects.create(
            org=self.org,
            project=self.project,
            name="Rig-Alpha",
        )
        self.nde_rig.refresh_from_db()
        NominalPipeOD.objects.create(
            org=self.org,
            label='12"',
            actual_od=Decimal("12.7500"),
            tolerance=Decimal("0.100"),
        )

    def _create_weld(
        self,
        weld_id: str,
        weld_date: date,
        length_inches: Decimal | None = None,
        *,
        welder: Welder | None = None,
    ) -> Weld:
        assigned_welder = welder or self.welder
        return Weld.objects.create(
            project=self.project,
            weld_id=weld_id,
            weld_date=weld_date,
            weld_length_inches=length_inches,
            primary_welder=assigned_welder,
            primary_stencil=assigned_welder.stencil if assigned_welder else None,
        )

    def test_daily_production_uses_weld_inches(self):
        for idx in range(3):
            self._create_weld(f"W36-{idx}", date(2024, 2, 1), Decimal("36"))
        for idx in range(5):
            self._create_weld(f"W02-{idx}", date(2024, 2, 1), Decimal("2"))

        analytics = build_dashboard_analytics(self.project, {})
        daily = analytics["daily_production"]
        self.assertEqual(len(daily), 1)
        self.assertEqual(daily[0]["weld_inches"], Decimal("118"))
        welder_series = analytics["welder_series"].get(self.welder.id)
        self.assertIsNotNone(welder_series)
        self.assertEqual(welder_series[-1]["cumulative_inches"], Decimal("118"))
        all_daily = analytics["all_welders_daily"]
        self.assertEqual(all_daily[0]["weld_inches_total"], Decimal("118.00"))
        median_series = analytics["welder_median_daily"]
        self.assertEqual(median_series[0]["median_daily_inches"], Decimal("118.00"))

    def test_planned_curve_generation(self):
        self.project.planned_welds_per_workday = Decimal("50")
        self.project.workdays_per_week = 5
        self.project.project_total_weld_inches = Decimal("60000")
        self.project.planned_start_date = date(2024, 1, 1)
        self.project.save()

        for idx in range(5):
            self._create_weld(f"Seed-{idx}", date(2023, 12, 20), Decimal("12"))

        analytics = build_dashboard_analytics(self.project, {})
        planner = analytics["planner_inputs"]
        self.assertEqual(planner["planned_daily_weld_inches"], Decimal("600.00"))
        self.assertEqual(
            planner["basis"],
            "Converted from planned weld count × project average weld length",
        )
        self.assertIsNone(planner["planned_weld_inches_per_workday"])
        planned_series = analytics["planned_series"]
        self.assertTrue(planned_series)
        self.assertEqual(planned_series[0]["weld_inches"], Decimal("600.00"))
        self.assertEqual(planned_series[-1]["weld_inches"], Decimal("60000.00"))

    def test_normalized_repair_rate(self):
        weld = self._create_weld("W5000", date(2024, 3, 10), Decimal("5000"))
        for idx in range(10):
            WeldRepair.objects.create(
                weld=weld,
                flagged_at=date(2024, 3, 11),
                repair_date=date(2024, 3, 11),
                defect_code_snapshot="POROSITY",
            )

        analytics = build_dashboard_analytics(self.project, {})
        self.assertEqual(analytics["normalized_repair_rate"], Decimal("2.00"))
        rate_series = analytics["repair_rate_series"]
        self.assertEqual(rate_series[0]["rate_per_1000_inches"], Decimal("2.00"))
        self.assertEqual(rate_series[0]["rolling_rate_7d"], Decimal("2.00"))
        self.assertFalse(rate_series[0]["weld_inches_zero"])

    def test_repair_clustering_pair_metrics(self):
        weld = self._create_weld("CLUST-01", date(2024, 5, 1), Decimal("50"))
        weld.heat_number = "HEAT-001"
        weld.pipe_size = "12\""
        weld.od = Decimal("12.750")
        weld.material1_wall_thickness_in = Decimal("0.375")
        weld.nde_rig = self.nde_rig
        weld.wps_document = self.wps_document
        weld.save()

        WeldRepair.objects.create(
            weld=weld,
            flagged_at=date(2024, 5, 2),
            repair_date=date(2024, 5, 2),
            defect_code_snapshot="INCLUSION",
            original_nderig=self.nde_rig.name,
        )
        WeldRepair.objects.create(
            weld=weld,
            flagged_at=date(2024, 5, 3),
            repair_date=date(2024, 5, 3),
            defect_code_snapshot="SLAG",
            original_nderig=self.nde_rig.name,
        )

        analytics = build_dashboard_analytics(self.project, {})
        heat_cluster = analytics["clustering"]["heat_number"][0]
        self.assertEqual(heat_cluster["weld_inches"], Decimal("50.00"))
        nominal_cluster = analytics["clustering"]["nominal_od_wall"][0]
        self.assertEqual(nominal_cluster["count"], 2)
        self.assertEqual(nominal_cluster["weld_inches"], Decimal("50.00"))
        self.assertEqual(
            nominal_cluster["repair_rate_per_1000_inches"],
            Decimal("40.00"),
        )
        heatmap_entry = analytics["heatmap"][0]
        self.assertEqual(heatmap_entry["weld_inches"], Decimal("50.00"))
        self.assertEqual(
            heatmap_entry["repair_rate_per_1000_inches"],
            Decimal("40.00"),
        )
        welder_wps = analytics["pair_clustering"]["welder_wps"][0]
        self.assertEqual(welder_wps["count"], 2)
        self.assertEqual(welder_wps["weld_inches"], Decimal("50.00"))
        nde_welder = analytics["pair_clustering"]["nde_welder"][0]
        self.assertEqual(nde_welder["count"], 2)
        nde_nominal = analytics["pair_clustering"]["nde_nominal"][0]
        self.assertEqual(nde_nominal["count"], 2)

    def test_length_fallback_marks_estimates(self):
        weld = Weld.objects.create(
            project=self.project,
            weld_id="W-OD",
            weld_date=date(2024, 4, 1),
            material1_outer_diameter_in=Decimal("10"),
            primary_welder=self.welder,
            primary_stencil=self.welder.stencil,
        )

        analytics = build_dashboard_analytics(self.project, {})
        length_info = analytics["length_info"][weld.id]
        self.assertTrue(length_info.estimated)
        self.assertEqual(length_info.source, Weld.WeldLengthSource.OUTER_DIAMETER)

    def test_planner_prefers_explicit_inches(self):
        self.project.planned_weld_inches_per_workday = Decimal("750")
        self.project.planned_welds_per_workday = Decimal("10")
        self.project.project_total_weld_inches = Decimal("30000")
        self.project.planned_start_date = date(2024, 2, 1)
        self.project.save()

        self._create_weld("Explicit-01", date(2024, 1, 5), Decimal("20"))

        analytics = build_dashboard_analytics(self.project, {})
        planner = analytics["planner_inputs"]
        self.assertEqual(planner["planned_weld_inches_per_workday"], Decimal("750"))
        self.assertEqual(planner["planned_daily_weld_inches"], Decimal("750.00"))
        self.assertEqual(
            planner["basis"], "User-defined planned weld inches per workday"
        )

    def test_repair_rate_zero_weld_inches_flagged(self):
        weld = self._create_weld("ZERO-LEN", date(2024, 6, 1), Decimal("0"))
        WeldRepair.objects.create(
            weld=weld,
            flagged_at=date(2024, 6, 2),
            repair_date=date(2024, 6, 2),
            defect_code_snapshot="POROSITY",
        )

        analytics = build_dashboard_analytics(self.project, {})
        series = analytics["repair_rate_series"]
        self.assertTrue(series[0]["weld_inches_zero"])
        self.assertEqual(series[0]["rate_per_1000_inches"], Decimal("1000.00"))

    def test_clustering_threshold_skips_singletons(self):
        weld = self._create_weld("SINGLE-01", date(2024, 7, 1), Decimal("25"))
        weld.heat_number = "ONE-OFF"
        weld.save(update_fields=["heat_number"])
        WeldRepair.objects.create(
            weld=weld,
            flagged_at=date(2024, 7, 2),
            repair_date=date(2024, 7, 2),
            defect_code_snapshot="SLAG",
        )

        analytics = build_dashboard_analytics(self.project, {})
        self.assertFalse(analytics["clustering"]["heat_number"])

    def test_welder_series_median_multiple_welders(self):
        welder_two = Welder.objects.create(
            org=self.org,
            name="Bobby Welder",
            stencil="BW2",
        )
        self._create_weld("MW-1", date(2024, 8, 1), Decimal("40"), welder=self.welder)
        self._create_weld("MW-2", date(2024, 8, 1), Decimal("20"), welder=welder_two)
        self._create_weld("MW-3", date(2024, 8, 2), Decimal("10"), welder=self.welder)
        self._create_weld("MW-4", date(2024, 8, 2), Decimal("30"), welder=welder_two)

        analytics = build_dashboard_analytics(self.project, {})
        median_lookup = {
            entry["date"]: entry["median_daily_inches"]
            for entry in analytics["welder_median_daily"]
        }
        self.assertEqual(median_lookup[date(2024, 8, 1)], Decimal("30.00"))
        self.assertEqual(median_lookup[date(2024, 8, 2)], Decimal("20.00"))


class WeldRepairModelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            email="qa@example.com",
            password="repair123",
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
            name="Pipeline QA",
            slug="pipeline-qa",
            created_by=self.user,
            status=Project.Status.ACTIVE,
        )
        self.weld = Weld.objects.create(
            project=self.project,
            weld_id="QA-1",
            weld_date=date(2024, 1, 1),
        )

    def test_attempt_updates_parent_metrics(self):
        repair = WeldRepair.objects.create(
            weld=self.weld,
            flagged_at=date(2024, 1, 2),
            repair_date=date(2024, 1, 2),
        )
        RepairAttempt.objects.create(
            repair=repair,
            performed_at=date(2024, 1, 3),
            welder_stencil="A1",
            inches_repaired=Decimal("2.50"),
            outcome=RepairAttempt.Outcome.FAIL,
        )
        repair.refresh_from_db()
        self.assertEqual(repair.attempt_count, 1)
        self.assertEqual(repair.total_inches_repaired, Decimal("2.50"))
        self.assertEqual(repair.last_attempt_date, date(2024, 1, 3))
        self.assertEqual(repair.repair_date, date(2024, 1, 3))

    def test_reinspection_pass_closes_repair(self):
        repair = WeldRepair.objects.create(
            weld=self.weld,
            flagged_at=date(2024, 1, 2),
        )
        Reinspection.objects.create(
            repair=repair,
            reinspection_date=date(2024, 1, 5),
            reinspection_nderig="Rig-1",
            reinspection_ndereport_ref="RPT-1",
            reinspection_result=Reinspection.Result.PASS,
        )
        repair.refresh_from_db()
        self.assertEqual(repair.status, WeldRepair.Status.CLOSED)
        self.assertEqual(repair.reinspection_passed_at, date(2024, 1, 5))

    def test_inspection_signal_creates_repair(self):
        WeldInspection.objects.create(
            weld=self.weld,
            inspection_date=date(2024, 1, 4),
            nde_type="UT",
            nde_rig="UT-1",
            report_reference="UT-1-001",
            result=WeldInspection.Result.REPAIR,
            requires_repair=True,
        )
        self.assertEqual(self.weld.repairs.count(), 1)
        repair = self.weld.repairs.first()
        self.assertEqual(repair.flagged_at, date(2024, 1, 4))
        self.assertEqual(repair.original_nderig, "UT-1")

    def test_weld_disposition_signal_creates_and_closes(self):
        self.weld.disposition = Weld.Disposition.REPAIR
        self.weld.save()
        self.assertEqual(self.weld.repairs.count(), 1)
        repair = self.weld.repairs.first()
        self.assertTrue(repair.manual_flag)
        self.weld.disposition = Weld.Disposition.ACCEPTED
        self.weld.save(update_fields=["disposition"])
        repair.refresh_from_db()
        self.assertEqual(repair.status, WeldRepair.Status.CLOSED)


class WeldRepairAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            email="qa@example.com",
            password="repair123",
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
            name="Pipeline Repairs",
            slug="pipeline-repairs",
            created_by=self.user,
            status=Project.Status.ACTIVE,
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMember.Role.PROJECT_MANAGER,
        )
        self.weld = Weld.objects.create(
            project=self.project,
            weld_id="REP-1",
            weld_date=date(2024, 2, 1),
        )
        self.client.force_login(self.user)

    def test_mark_repair_idempotent(self):
        url = reverse(
            "welds:mark_weld_for_repair",
            kwargs={"org_slug": self.org.slug, "weld_id": self.weld.id},
        )
        response = self.client.post(url, data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 201)
        repair_id = response.json()["repair"]["id"]
        second = self.client.post(url, data="{}", content_type="application/json")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["repair"]["id"], repair_id)

    def test_repair_grid_flow(self):
        mark_url = reverse(
            "welds:mark_weld_for_repair",
            kwargs={"org_slug": self.org.slug, "weld_id": self.weld.id},
        )
        self.client.post(mark_url, data="{}", content_type="application/json")
        repair = self.weld.repairs.first()

        patch_url = reverse(
            "welds:update_repair",
            kwargs={"org_slug": self.org.slug, "repair_id": repair.id},
        )
        payload = {"comments": "Needs follow-up", "nde_type": "UT"}
        response = self.client.patch(
            patch_url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        repair.refresh_from_db()
        self.assertEqual(repair.comments, "Needs follow-up")
        self.assertEqual(repair.nde_type, "UT")

        attempt_url = reverse(
            "welds:repair_add_attempt",
            kwargs={"org_slug": self.org.slug, "repair_id": repair.id},
        )
        attempt_payload = {
            "performed_at": "2024-02-05",
            "welder_stencil": "A1",
            "inches_repaired": "2.5",
            "outcome": RepairAttempt.Outcome.FAIL,
        }
        response = self.client.post(
            attempt_url,
            data=json.dumps(attempt_payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        repair.refresh_from_db()
        self.assertEqual(repair.attempt_count, 1)

        reinspect_url = reverse(
            "welds:repair_add_reinspection",
            kwargs={"org_slug": self.org.slug, "repair_id": repair.id},
        )
        reinspect_payload = {
            "reinspection_date": "2024-02-10",
            "reinspection_nderig": "UT-1",
            "reinspection_ndereport_ref": "UT-1-002",
            "reinspection_result": Reinspection.Result.PASS,
        }
        response = self.client.post(
            reinspect_url,
            data=json.dumps(reinspect_payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        repair.refresh_from_db()
        self.assertEqual(repair.status, WeldRepair.Status.CLOSED)

        list_url = reverse(
            "welds:project_repairs",
            kwargs={"org_slug": self.org.slug, "project_id": self.project.id},
        )
        list_response = self.client.get(list_url)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["total_rows"], 1)


class WeldDashboardDrilldownSelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            email="dashboard@example.com",
            password="pass1234",
        )
        cls.org = Organization.objects.create(
            name="Acme Pipeline",
            slug="acme-pipeline",
            owner=cls.user,
        )
        Membership.objects.create(
            org=cls.org,
            user=cls.user,
            role=Membership.Role.ADMIN,
        )
        cls.project = Project.objects.create(
            org=cls.org,
            name="Pipeline A",
            slug="pipeline-a",
            created_by=cls.user,
            status=Project.Status.ACTIVE,
        )
        ProjectMember.objects.create(
            project=cls.project,
            user=cls.user,
            role=ProjectMember.Role.PROJECT_MANAGER,
        )
        cls.other_project = Project.objects.create(
            org=cls.org,
            name="Pipeline B",
            slug="pipeline-b",
            created_by=cls.user,
            status=Project.Status.ACTIVE,
        )
        cls.weld_a = Weld.objects.create(
            project=cls.project,
            weld_id="W-001",
            heat_number="H-100",
            weld_length_inches=Decimal("10.0"),
            weld_date=date(2023, 1, 1),
        )
        cls.weld_b = Weld.objects.create(
            project=cls.project,
            weld_id="W-002",
            heat_number="H-100",
            weld_length_inches=Decimal("12.0"),
            weld_date=date(2023, 1, 2),
        )
        cls.weld_c = Weld.objects.create(
            project=cls.project,
            weld_id="W-003",
            heat_number="H-200",
            weld_length_inches=Decimal("14.0"),
            weld_date=date(2023, 1, 3),
        )
        cls.cross_project_weld = Weld.objects.create(
            project=cls.other_project,
            weld_id="W-900",
            heat_number="H-100",
            weld_length_inches=Decimal("16.0"),
            weld_date=date(2023, 1, 4),
        )
        cls.repair_a = WeldRepair.objects.create(
            weld=cls.weld_a,
            flagged_at=date(2023, 1, 5),
            repair_stencil="R1",
        )
        cls.repair_b = WeldRepair.objects.create(
            weld=cls.weld_b,
            flagged_at=date(2023, 1, 6),
            repair_stencil="R2",
        )
        cls.repair_c = WeldRepair.objects.create(
            weld=cls.weld_c,
            flagged_at=date(2023, 1, 7),
            repair_stencil="R3",
        )
        cls.cross_repair = WeldRepair.objects.create(
            weld=cls.cross_project_weld,
            flagged_at=date(2023, 1, 8),
            repair_stencil="RX",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _url(self):
        return reverse(
            "welds:weld_dashboard_drilldown",
            kwargs={
                "org_slug": self.org.slug,
                "project_slug": self.project.slug,
            },
        )

    def _assert_repair_weld_ids(self, response, expected_weld_ids):
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total_count"], len(expected_weld_ids))
        self.assertEqual(
            {row["weld_id"] for row in payload["results"]},
            set(expected_weld_ids),
        )

    def test_accepts_cluster_keys_in_json_body(self):
        payload = {
            "dimension": "heat_number",
            "cluster_keys": {"heat_number": "H-100"},
        }
        response = self.client.post(
            self._url(),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self._assert_repair_weld_ids(
            response, {self.weld_a.weld_id, self.weld_b.weld_id}
        )

    def test_accepts_cluster_keys_as_query_string(self):
        response = self.client.get(
            self._url(),
            {
                "dimension": "heat_number",
                "cluster_keys": json.dumps({"heat_number": "H-100"}),
            },
        )
        self._assert_repair_weld_ids(
            response, {self.weld_a.weld_id, self.weld_b.weld_id}
        )

    def test_falls_back_to_key_parameter(self):
        response = self.client.get(
            self._url(),
            {"dimension": "heat_number", "key": "H-100"},
        )
        self._assert_repair_weld_ids(
            response, {self.weld_a.weld_id, self.weld_b.weld_id}
        )

    def test_returns_empty_results_when_cluster_missing(self):
        response = self.client.get(self._url(), {"dimension": "heat_number"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total_count"], 0)

    def test_ignores_repairs_from_other_projects(self):
        response = self.client.get(
            self._url(),
            {"dimension": "heat_number", "key": "H-100"},
        )
        ids = {row["weld_id"] for row in response.json()["results"]}
        self.assertNotIn(self.cross_project_weld.weld_id, ids)


class WeldDashboardAnalyticsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            email="analytics@example.com",
            password="pass1234",
        )
        self.org = Organization.objects.create(
            name="Acme", slug="acme-analytics", owner=self.user
        )
        Membership.objects.create(
            org=self.org,
            user=self.user,
            role=Membership.Role.ADMIN,
        )
        self.project = Project.objects.create(
            org=self.org,
            name="Pipeline B",
            slug="pipeline-b",
            created_by=self.user,
            status=Project.Status.ACTIVE,
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMember.Role.MEMBER,
        )
        self.client.force_login(self.user)

        self.welder = Welder.objects.create(
            org=self.org,
            name="Alice Welder",
            stencil="A100",
        )
        self.second_welder = Welder.objects.create(
            org=self.org,
            name="Bob Welder",
            stencil="B200",
        )
        self.nde_rig = NDERig.objects.create(
            org=self.org,
            project=self.project,
            name="Rig-1",
        )
        self.nde_rig.refresh_from_db()

        NominalPipeOD.objects.create(
            org=self.org,
            label='6"',
            actual_od=Decimal("6.6250"),
            tolerance=Decimal("0.100"),
        )
        NominalPipeOD.objects.create(
            org=self.org,
            label='10"',
            actual_od=Decimal("10.7500"),
            tolerance=Decimal("0.100"),
        )

        self.start_date = date(2024, 1, 1)
        self.end_date = date(2024, 1, 31)

    def _create_weld(
        self,
        weld_id: str,
        od: Decimal,
        wall: Decimal,
        length: Decimal,
        *,
        welder: Welder | None = None,
    ) -> Weld:
        weld = Weld.objects.create(
            project=self.project,
            weld_id=weld_id,
            weld_length_inches=length,
            weld_length_source=Weld.WeldLengthSource.MEASURED,
            weld_length_basis="Measured",
            primary_welder=welder or self.welder,
            date_welded=self.start_date,
            od=od,
            material1_wall_thickness_in=wall,
            nde_rig=self.nde_rig,
        )
        weld.refresh_from_db()
        return weld

    def _create_repair(self, weld: Weld, flagged_at: date, nde_rig_label: str) -> WeldRepair:
        return WeldRepair.objects.create(
            weld=weld,
            flagged_at=flagged_at,
            original_nderig=nde_rig_label,
            repair_stencil="R100",
            defect_code_snapshot="DC-1",
        )

    def _create_sample_data(self):
        weld_one = self._create_weld(
            "W-100",
            Decimal("6.6250"),
            Decimal("0.280"),
            Decimal("10.0"),
        )
        weld_two = self._create_weld(
            "W-200",
            Decimal("10.7500"),
            Decimal("0.500"),
            Decimal("20.0"),
            welder=self.second_welder,
        )
        repair_a = self._create_repair(weld_one, self.start_date, "Rig-1")
        repair_b = self._create_repair(weld_one, self.start_date + timedelta(days=1), "Rig-1")
        repair_c = self._create_repair(weld_two, self.start_date + timedelta(days=2), "Rig-2")
        return weld_one, weld_two, (repair_a, repair_b, repair_c)

    def test_weld_populates_nominal_and_wall(self):
        weld = self._create_weld(
            "W-300",
            Decimal("6.6250"),
            Decimal("0.281"),
            Decimal("12.0"),
        )
        self.assertEqual(weld.nominal_od, '6"')
        self.assertEqual(weld.wall_thickness_norm, Decimal("0.281"))

    def test_dashboard_clusters_include_nominal_wall_bucket(self):
        weld_one, _, repairs = self._create_sample_data()
        filters = {"start_date": self.start_date, "end_date": self.end_date}
        analytics = build_dashboard_analytics(self.project, filters)
        nominal_clusters = analytics["clustering"]["nominal_od_wall"]
        bucket = next(
            (
                item
                for item in nominal_clusters
                if item.get("nominal_od") == '6"'
                and item.get("wall_thickness_norm") == "0.280"
            ),
            None,
        )
        self.assertIsNotNone(bucket)
        self.assertEqual(bucket["count"], 2)
        self.assertEqual(bucket["label"], '6" × 0.280')
        self.assertEqual(Decimal(str(bucket["weld_inches"])), Decimal("10.00"))
        self.assertEqual(
            Decimal(str(bucket["repair_rate_per_1000_inches"])),
            Decimal("200.00"),
        )

        welder_pair = analytics["pair_clustering"]["welder_wps"]
        entry = next(
            (
                item
                for item in welder_pair
                if item.get("welder") == self.welder.stencil
            ),
            None,
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry["count"], 2)

        nde_nominal = analytics["pair_clustering"]["nde_nominal"]
        nde_entry = next(
            (
                item
                for item in nde_nominal
                if item.get("nde_rig") == "Rig-1"
                and item.get("nominal_od") == '6"'
            ),
            None,
        )
        self.assertIsNotNone(nde_entry)
        self.assertEqual(nde_entry["count"], 2)

    def test_build_drilldown_filters_repairs(self):
        weld_one, weld_two, repairs = self._create_sample_data()
        filters = {"start_date": self.start_date, "end_date": self.end_date}
        result = build_drilldown(
            self.project,
            filters,
            "nominal_od_wall",
            {"nominal_od": '6"', "wall_thickness_norm": "0.280"},
        )
        self.assertEqual(result["total_count"], 2)
        returned_ids = {row["id"] for row in result["results"]}
        self.assertSetEqual(returned_ids, {repairs[0].id, repairs[1].id})
        self.assertTrue(all(row["weld_id"] == weld_one.weld_id for row in result["results"]))

        paginated = build_drilldown(
            self.project,
            filters,
            "nominal_od_wall",
            {"nominal_od": '6"', "wall_thickness_norm": "0.280"},
            page=1,
            page_size=1,
            sort="flagged_at",
        )
        self.assertEqual(paginated["total_count"], 2)
        self.assertEqual(len(paginated["results"]), 1)
        second_page = build_drilldown(
            self.project,
            filters,
            "nominal_od_wall",
            {"nominal_od": '6"', "wall_thickness_norm": "0.280"},
            page=2,
            page_size=1,
            sort="flagged_at",
        )
        self.assertEqual(len(second_page["results"]), 1)
        combined_ids = {
            paginated["results"][0]["id"],
            second_page["results"][0]["id"],
        }
        self.assertSetEqual(combined_ids, {repairs[0].id, repairs[1].id})

        other_cluster = build_drilldown(
            self.project,
            filters,
            "nominal_od_wall",
            {"nominal_od": '10"', "wall_thickness_norm": "0.500"},
        )
        self.assertEqual(other_cluster["total_count"], 1)
        self.assertEqual(other_cluster["results"][0]["id"], repairs[2].id)
        self.assertEqual(other_cluster["results"][0]["weld_id"], weld_two.weld_id)

    def test_drilldown_view_returns_paginated_results_and_csv(self):
        weld_one, _, _ = self._create_sample_data()
        url = reverse(
            "welds:weld_dashboard_drilldown",
            kwargs={
                "org_slug": self.org.slug,
                "project_slug": self.project.slug,
            },
        )
        params = {
            "dimension": "nominal_od_wall",
            "cluster_keys": json.dumps({"nominal_od": '6"', "wall_thickness_norm": "0.280"}),
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
        }
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total_count"], 2)
        self.assertTrue(all(item["nominal_od"] == '6"' for item in payload["results"]))

        csv_params = params | {"format": "csv"}
        csv_response = self.client.get(url, csv_params)
        self.assertEqual(csv_response.status_code, 200)
        self.assertEqual(
            csv_response["Content-Type"],
            "text/csv",
        )
        content = csv_response.content.decode("utf-8")
        self.assertIn("repair_id,weld_id,flagged_at", content)
        self.assertIn(weld_one.weld_id, content)
