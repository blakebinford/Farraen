from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from drive.models import Folder
from organizations.models import Membership, Organization

from .models import Project, ProjectMember


class ProjectRoleSyncTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.owner = self.User.objects.create_user(
            email="owner@example.com",
            password="pass1234",
        )
        self.org = Organization.objects.create(
            name="Role Org",
            slug="role-org",
            owner=self.owner,
        )
        Membership.objects.create(
            org=self.org,
            user=self.owner,
            role=Membership.Role.ADMIN,
        )

    def test_sync_role_memberships_assigns_roles(self):
        manager = self.User.objects.create_user(email="pm@example.com")
        superintendent = self.User.objects.create_user(email="sup@example.com")
        quality_manager = self.User.objects.create_user(email="qm@example.com")
        tech_a = self.User.objects.create_user(email="qt1@example.com")
        tech_b = self.User.objects.create_user(email="qt2@example.com")

        project = Project.objects.create(
            org=self.org,
            name="Sync Project",
            slug="sync-project",
            created_by=self.owner,
            status=Project.Status.ACTIVE,
            project_manager=manager,
            superintendent=superintendent,
            quality_manager=quality_manager,
        )
        project.quality_techs.set([tech_a, tech_b])
        project.sync_role_memberships()

        membership_roles = {
            (m.user_id, m.role)
            for m in ProjectMember.objects.filter(project=project)
        }
        self.assertIn((manager.id, ProjectMember.Role.PROJECT_MANAGER), membership_roles)
        self.assertIn((superintendent.id, ProjectMember.Role.SUPERINTENDENT), membership_roles)
        self.assertIn((quality_manager.id, ProjectMember.Role.QUALITY_MANAGER), membership_roles)
        self.assertIn((tech_a.id, ProjectMember.Role.QUALITY_TECH), membership_roles)
        self.assertIn((tech_b.id, ProjectMember.Role.QUALITY_TECH), membership_roles)

        project.quality_techs.remove(tech_b)
        project.sync_role_memberships()
        membership = ProjectMember.objects.get(project=project, user=tech_b)
        self.assertEqual(membership.role, ProjectMember.Role.MEMBER)


class ProjectArchiveLockTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.owner = self.User.objects.create_user(
            email="archive-owner@example.com",
            password="pass1234",
        )
        self.org = Organization.objects.create(
            name="Archive Org",
            slug="archive-org",
            owner=self.owner,
        )
        Membership.objects.create(
            org=self.org,
            user=self.owner,
            role=Membership.Role.ADMIN,
        )
        self.project = Project.objects.create(
            org=self.org,
            name="Archive Project",
            slug="archive-project",
            created_by=self.owner,
            status=Project.Status.ACTIVE,
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.owner,
            role=ProjectMember.Role.PROJECT_MANAGER,
        )
        self.folder = Folder.objects.filter(project=self.project).first()

    def test_archived_project_blocks_child_updates(self):
        self.project.status = Project.Status.ARCHIVED
        self.project.save(allow_archived_change=True)
        self.folder.name = "Updated"
        with self.assertRaises(PermissionDenied):
            self.folder.save()

    def test_archived_project_blocks_direct_updates(self):
        self.project.status = Project.Status.ARCHIVED
        self.project.save(allow_archived_change=True)
        self.project.description = "Cannot change"
        with self.assertRaises(PermissionDenied):
            self.project.save()

    def test_unarchive_allows_updates_with_flag(self):
        self.project.status = Project.Status.ARCHIVED
        self.project.save(allow_archived_change=True)
        self.project.status = Project.Status.ACTIVE
        self.project.description = "Back in action"
        self.project.save(allow_archived_change=True)
        self.project.refresh_from_db()
        self.assertEqual(self.project.description, "Back in action")
        self.assertEqual(self.project.status, Project.Status.ACTIVE)

    def test_updated_by_tracks_changes(self):
        self.project.updated_by = self.owner
        self.project.description = "Updated"
        self.project.save()
        self.project.refresh_from_db()
        self.assertEqual(self.project.updated_by, self.owner)
        self.assertIsNotNone(self.project.updated_at)
