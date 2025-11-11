import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from drive.models import FileNode, Folder
from organizations.models import Membership, Organization
from projects.models import Project, ProjectMember

from .models import MaterialHeat, NDERig, Welder, Weld, WeldHistory
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
            is_archived=False,
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
            is_archived=False,
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
            is_archived=False,
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
        ).update(role=ProjectMember.Role.MANAGER)
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
        ).update(role=ProjectMember.Role.MANAGER)
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
        ).update(role=ProjectMember.Role.MANAGER)
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

