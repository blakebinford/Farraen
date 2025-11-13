from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile

from organizations.models import Organization, Membership
from projects.models import Project, ProjectMember
from drive.models import Folder, FileNode, FileVersion, FileEvent


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

    def test_file_detail_drawer_endpoint(self):
        response = self.client.get(self._drawer_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "drive-drawer__header")
        self.assertContains(response, "Versions")

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
