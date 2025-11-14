from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from datetime import timedelta

from organizations.models import Organization, Membership
from projects.models import Project, ProjectMember
from drive.models import Folder, FileNode, FileVersion, FileEvent
from welds.models import MaterialHeatDraft


class DriveUIViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(email="member@example.com", password="testpass123")
        self.org = Organization.objects.create(name="Acme", slug="acme", owner=self.user)
        Membership.objects.create(org=self.org, user=self.user, role=Membership.Role.MEMBER)
        self.project = Project.objects.create(org=self.org, name="Bridge", slug="bridge", created_by=self.user)
        ProjectMember.objects.create(project=self.project, user=self.user)
        self.folder = Folder.objects.create(org=self.org, project=self.project, name="Drawings", created_by=self.user)

        self.node = FileNode.objects.create(
            org=self.org,
            project=self.project,
            folder=self.folder,
            name="A-001.pdf",
            doc_type="MTR",
            created_by=self.user,
        )
        version = FileVersion.objects.create(
            file_node=self.node,
            version=1,
            blob=ContentFile(b"hello", name="a-001.pdf"),
            size=5,
            content_type="application/pdf",
            uploaded_by=self.user,
        )
        self.node.latest_version = version
        self.node.size = version.size
        self.node.save(update_fields=["latest_version", "size"])

        self.client.force_login(self.user)

    def _folder_url(self):
        return reverse("drive_folder", kwargs={
            "org_slug": self.org.slug,
            "project_slug": self.project.slug,
            "folder_id": self.folder.id,
        })

    def _drawer_url(self):
        return reverse("drive_file_drawer", kwargs={
            "org_slug": self.org.slug,
            "project_slug": self.project.slug,
            "file_id": self.node.id,
        })

    def test_folder_view_renders_new_layout(self):
        response = self.client.get(self._folder_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "drive-left-nav")
        self.assertContains(response, "drive-topbar")
        self.assertContains(response, "drive-grid-view")
        self.assertContains(response, "drive-drawer-host")
        self.assertContains(response, "static/css/main.css")
        self.assertContains(response, "bootstrap-icons")
        self.assertContains(response, "site-topbar")

    def test_upload_button_uses_modal(self):
        response = self.client.get(self._folder_url())
        self.assertEqual(response.status_code, 200)
        upload_url = reverse(
            "drive_upload",
            kwargs={
                "org_slug": self.org.slug,
                "project_slug": self.project.slug,
                "folder_id": self.folder.id,
            },
        )
        self.assertNotContains(response, f'href="{upload_url}"')
        self.assertContains(response, "data-bs-target=\"#drive-upload-modal\"")
        self.assertContains(response, f'action="{upload_url}"')

    def test_upload_creates_version_and_event(self):
        upload_url = reverse(
            "drive_upload",
            kwargs={
                "org_slug": self.org.slug,
                "project_slug": self.project.slug,
                "folder_id": self.folder.id,
            },
        )
        payload = {
            "file": SimpleUploadedFile(
                "report.pdf", b"%PDF-1.4 test", content_type="application/pdf"
            ),
            "note": "Test upload",
        }
        response = self.client.post(upload_url, payload)
        self.assertEqual(response.status_code, 302)

        node = FileNode.objects.get(folder=self.folder, name="report.pdf")
        version = node.latest_version
        self.assertIsNotNone(version)
        self.assertEqual(version.version, 1)
        self.assertTrue(
            FileEvent.objects.filter(
                file_node=node,
                action=FileEvent.Action.UPLOAD,
                version=version,
            ).exists()
        )

    @patch("welds.signals.process_mtr_fileversion.delay")
    def test_upload_mtr_redirects_to_drafts_with_metadata(self, mock_delay):
        upload_url = reverse(
            "drive_upload",
            kwargs={
                "org_slug": self.org.slug,
                "project_slug": self.project.slug,
                "folder_id": self.folder.id,
            },
        )
        payload = {
            "file": SimpleUploadedFile(
                "mtr-report.pdf", b"%PDF-1.4 mtr", content_type="application/pdf"
            ),
            "doc_type": FileNode.DocType.MTR,
            "number": "MTR-42",
            "title": "QA Heat",
        }
        response = self.client.post(upload_url, payload)
        node = FileNode.objects.get(folder=self.folder, name="mtr-report.pdf")
        expected_url = (
            reverse("welds:mtr_draft_list", kwargs={"org_slug": self.org.slug})
            + f"?file={node.id}"
        )
        self.assertRedirects(response, expected_url, fetch_redirect_response=False)
        self.assertEqual(node.doc_type, FileNode.DocType.MTR)
        self.assertEqual(node.number, "MTR-42")
        self.assertEqual(node.title, "QA Heat")
        self.assertIsNotNone(node.latest_version)
        self.assertEqual(node.latest_version.uploaded_by, self.user)
        mock_delay.assert_called_once_with(node.latest_version.id)

    @patch("welds.signals.process_mtr_fileversion.delay")
    def test_upload_mtr_rejected_for_org_folder(self, mock_delay):
        org_folder = Folder.objects.create(
            org=self.org,
            project=None,
            name="Org Docs",
            created_by=self.user,
        )
        upload_url = reverse(
            "drive_upload",
            kwargs={
                "org_slug": self.org.slug,
                "project_slug": self.project.slug,
                "folder_id": org_folder.id,
            },
        )
        payload = {
            "file": SimpleUploadedFile(
                "blocked.pdf", b"%PDF-1.4 blocked", content_type="application/pdf"
            ),
            "doc_type": FileNode.DocType.MTR,
        }
        response = self.client.post(upload_url, payload)
        expected_url = reverse(
            "project_drive_root",
            kwargs={"org_slug": self.org.slug, "project_slug": self.project.slug},
        )
        self.assertRedirects(response, expected_url, fetch_redirect_response=False)
        self.assertFalse(
            FileNode.objects.filter(
                folder=org_folder, doc_type=FileNode.DocType.MTR
            ).exists()
        )
        mock_delay.assert_not_called()

    def test_file_mtr_status_endpoint(self):
        status_url = reverse(
            "drive_mtr_status",
            kwargs={
                "org_slug": self.org.slug,
                "project_slug": self.project.slug,
                "file_id": self.node.id,
            },
        )
        MaterialHeatDraft.objects.create(
            file_node=self.node,
            org=self.org,
            heat_number="HX-1",
        )
        MaterialHeatDraft.objects.create(
            file_node=self.node,
            org=self.org,
            heat_number="HX-2",
            verified=True,
        )

        response = self.client.get(status_url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["drafts_count"], 1)
        self.assertFalse(payload["mtr_approved"])

        self.node.mtr_approved = True
        self.node.save(update_fields=["mtr_approved"])

        response = self.client.get(status_url)
        payload = response.json()
        self.assertTrue(payload["mtr_approved"])

    def test_file_detail_drawer_endpoint(self):
        response = self.client.get(self._drawer_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "drive-drawer__header")
        self.assertContains(response, "Versions")
        self.assertContains(response, "drive-preview")

    def test_file_detail_page_standalone(self):
        detail_url = reverse("drive_file", kwargs={
            "org_slug": self.org.slug,
            "project_slug": self.project.slug,
            "file_id": self.node.id,
        })
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "drive-drawer--standalone")
        self.assertContains(response, "Download latest")

    def test_revert_creates_new_version_and_event(self):
        url = reverse("drive_file_revert_version", kwargs={
            "org_slug": self.org.slug,
            "project_slug": self.project.slug,
            "file_id": self.node.id,
            "version": 1,
        })
        response = self.client.post(url, {"note": "Restore"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        self.node.refresh_from_db()
        self.assertEqual(self.node.version, 2)
        self.assertTrue(FileEvent.objects.filter(file_node=self.node, action=FileEvent.Action.REVERT).exists())
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_checkout_flow_sets_flag_and_event(self):
        checkout_url = reverse("drive_file_checkout", kwargs={
            "org_slug": self.org.slug,
            "project_slug": self.project.slug,
            "file_id": self.node.id,
        })
        response = self.client.post(checkout_url)
        self.assertEqual(response.status_code, 302)
        self.node.refresh_from_db()
        self.assertEqual(self.node.checked_out_by_id, self.user.id)
        self.assertTrue(FileEvent.objects.filter(file_node=self.node, action=FileEvent.Action.CHECKOUT).exists())

    def test_left_nav_scopes_to_project_top_level(self):
        other_project = Project.objects.create(
            org=self.org,
            name="Tunnel",
            slug="tunnel",
            created_by=self.user,
        )
        ProjectMember.objects.create(project=other_project, user=self.user)
        other_folder = Folder.objects.create(
            org=self.org,
            project=other_project,
            name="Specs",
            created_by=self.user,
        )

        legacy_folder = Folder.objects.create(
            org=self.org,
            project=self.project,
            name="Legacy",
            created_by=self.user,
        )

        archived_root = Folder.objects.create(
            org=self.org,
            project=self.project,
            name=self.project.name,
            created_by=self.user,
        )
        archived_root.is_archived = True
        archived_root.save(update_fields=["is_archived"])

        response = self.client.get(self._folder_url())
        self.assertEqual(response.status_code, 200)
        tree = list(response.context["folder_tree"])
        ids = {folder.id for folder in tree}
        self.assertIn(self.folder.id, ids)
        self.assertIn(legacy_folder.id, ids)
        self.assertNotIn(other_folder.id, ids)
        self.assertNotIn(archived_root.id, ids)
        self.assertTrue(all(folder.project_id == self.project.id for folder in tree))
        self.assertTrue(all(not folder.is_archived for folder in tree))

    def test_doc_type_quick_filter(self):
        other = FileNode.objects.create(
            org=self.org,
            project=self.project,
            folder=self.folder,
            name="B-002.pdf",
            doc_type="WPS",
            created_by=self.user,
        )
        other_version = FileVersion.objects.create(
            file_node=other,
            version=1,
            blob=ContentFile(b"wps", name="b-002.pdf"),
            size=3,
            content_type="application/pdf",
            uploaded_by=self.user,
        )
        other.latest_version = other_version
        other.save(update_fields=["latest_version", "size"])

        response = self.client.get(self._folder_url(), {"doc_type": "MTR"})
        self.assertEqual(response.status_code, 200)
        files = list(response.context["files"])
        self.assertEqual([self.node.id], [f.id for f in files])

    def test_checked_out_quick_filter(self):
        other = FileNode.objects.create(
            org=self.org,
            project=self.project,
            folder=self.folder,
            name="B-003.pdf",
            doc_type="MTR",
            created_by=self.user,
        )
        FileVersion.objects.create(
            file_node=other,
            version=1,
            blob=ContentFile(b"mtr", name="b-003.pdf"),
            size=3,
            content_type="application/pdf",
            uploaded_by=self.user,
        )
        self.node.checked_out_by = self.user
        self.node.checked_out_at = timezone.now()
        self.node.save(update_fields=["checked_out_by", "checked_out_at"])

        response = self.client.get(self._folder_url(), {"filter": "checkedout"})
        self.assertEqual(response.status_code, 200)
        files = list(response.context["files"])
        self.assertEqual([self.node.id], [f.id for f in files])

    def test_trash_quick_filter(self):
        other = FileNode.objects.create(
            org=self.org,
            project=self.project,
            folder=self.folder,
            name="B-004.pdf",
            doc_type="MTR",
            created_by=self.user,
        )
        FileVersion.objects.create(
            file_node=other,
            version=1,
            blob=ContentFile(b"mtr", name="b-004.pdf"),
            size=3,
            content_type="application/pdf",
            uploaded_by=self.user,
        )

        self.node.is_archived = True
        self.node.save(update_fields=["is_archived"])

        response = self.client.get(self._folder_url(), {"filter": "trash"})
        self.assertEqual(response.status_code, 200)
        files = list(response.context["files"])
        self.assertEqual([self.node.id], [f.id for f in files])

    def test_recent_quick_filter(self):
        old = FileNode.objects.create(
            org=self.org,
            project=self.project,
            folder=self.folder,
            name="B-005.pdf",
            doc_type="MTR",
            created_by=self.user,
        )
        old_version = FileVersion.objects.create(
            file_node=old,
            version=1,
            blob=ContentFile(b"old", name="b-005.pdf"),
            size=3,
            content_type="application/pdf",
            uploaded_by=self.user,
        )
        FileVersion.objects.filter(pk=old_version.pk).update(
            created_at=timezone.now() - timedelta(days=45)
        )

        response = self.client.get(self._folder_url(), {"filter": "recent"})
        self.assertEqual(response.status_code, 200)
        files = list(response.context["files"])
        self.assertEqual([self.node.id], [f.id for f in files])

    def test_recently_viewed_context_unique_and_ordered(self):
        nodes = []
        for idx in range(6):
            node = FileNode.objects.create(
                org=self.org,
                project=self.project,
                folder=self.folder,
                name=f"Doc-{idx}.pdf",
                created_by=self.user,
            )
            nodes.append(node)

        now = timezone.now()
        for idx, node in enumerate(nodes):
            FileEvent.objects.create(
                org=self.org,
                file_node=node,
                actor=self.user,
                action=FileEvent.Action.VIEW,
                at=now - timedelta(minutes=idx + 1),
            )

        FileEvent.objects.create(
            org=self.org,
            file_node=nodes[2],
            actor=self.user,
            action=FileEvent.Action.VIEW,
            at=now - timedelta(seconds=30),
        )

        archived = FileNode.objects.create(
            org=self.org,
            project=self.project,
            folder=self.folder,
            name="Archived.pdf",
            created_by=self.user,
            is_archived=True,
        )
        FileEvent.objects.create(
            org=self.org,
            file_node=archived,
            actor=self.user,
            action=FileEvent.Action.VIEW,
            at=now - timedelta(seconds=10),
        )

        other_project = Project.objects.create(
            org=self.org,
            name="Other",
            slug="other",
            created_by=self.user,
        )
        other_folder = Folder.objects.create(
            org=self.org,
            project=other_project,
            name="Specs",
            created_by=self.user,
        )
        other_node = FileNode.objects.create(
            org=self.org,
            project=other_project,
            folder=other_folder,
            name="Other.pdf",
            created_by=self.user,
        )
        FileEvent.objects.create(
            org=self.org,
            file_node=other_node,
            actor=self.user,
            action=FileEvent.Action.VIEW,
            at=now - timedelta(seconds=5),
        )

        response = self.client.get(self._folder_url())
        self.assertEqual(response.status_code, 200)

        recent = response.context["recently_viewed"]
        self.assertEqual(len(recent), 5)
        recent_ids = [item["node"].id for item in recent]
        expected_order = [
            nodes[2].id,
            nodes[0].id,
            nodes[1].id,
            nodes[3].id,
            nodes[4].id,
        ]
        self.assertEqual(recent_ids, expected_order)
        self.assertTrue(all("last_at" in item for item in recent))

    def test_recently_viewed_left_nav_rendering(self):
        FileEvent.objects.create(
            org=self.org,
            file_node=self.node,
            actor=self.user,
            action=FileEvent.Action.VIEW,
            at=timezone.now(),
        )

        response = self.client.get(self._folder_url())
        self.assertEqual(response.status_code, 200)
        drawer_url = reverse("drive_file_drawer", kwargs={
            "org_slug": self.org.slug,
            "project_slug": self.project.slug,
            "file_id": self.node.id,
        })
        self.assertContains(response, "Recently viewed")
        self.assertContains(response, drawer_url)
        self.assertContains(response, self.node.name)

    def test_recently_viewed_empty_state(self):
        response = self.client.get(self._folder_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No recent documents.")
