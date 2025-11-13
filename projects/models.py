from __future__ import annotations

from typing import Iterable

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from django.utils.text import slugify


class Tag(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


class Project(models.Model):
    class Status(models.TextChoices):
        PLANNED = "PLANNED", "Planned"
        ACTIVE = "ACTIVE", "Active"
        ON_HOLD = "ON_HOLD", "On hold"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"
        ARCHIVED = "ARCHIVED", "Archived"

    org = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="projects",
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PLANNED,
        db_index=True,
    )
    description = models.TextField(
        null=True,
        blank=True,
        help_text="Short description of the project.",
    )
    project_code = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        db_index=True,
        help_text="Optional internal project code",
    )
    planned_start_date = models.DateField(
        null=True,
        blank=True,
        help_text="Planned start date for weld production.",
    )
    planned_end_date = models.DateField(null=True, blank=True)
    client_name = models.CharField(max_length=200, null=True, blank=True)
    site_address = models.CharField(max_length=300, null=True, blank=True)
    site_latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    site_longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    external_id = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        db_index=True,
    )
    project_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="managed_projects",
    )
    superintendent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="superintended_projects",
    )
    quality_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="quality_managed_projects",
    )
    quality_techs = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="quality_tech_projects",
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="projects")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_projects",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    planned_welds_per_workday = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Planned weld count per productive workday.",
    )
    workdays_per_week = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Number of planned workdays per calendar week.",
    )
    project_total_weld_inches = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Total weld inches planned for the project scope.",
    )

    class Meta:
        unique_together = [("org", "slug")]
        ordering = ["name"]
        permissions = [
            ("can_unarchive_project", "Can unarchive archived projects"),
        ]

    def save(self, *args, **kwargs):
        allow_archived_change = kwargs.pop("allow_archived_change", False)
        if not self.slug:
            self.slug = slugify(self.name)[:200] or "project"
        if self.pk:
            previous = Project.objects.filter(pk=self.pk).only("status").first()
            if previous and previous.status == Project.Status.ARCHIVED:
                # Block changes unless explicitly allowed
                if self.status != Project.Status.ARCHIVED and not allow_archived_change:
                    raise PermissionDenied("Unarchiving requires elevated permissions.")
                if self.status == Project.Status.ARCHIVED and not allow_archived_change:
                    raise PermissionDenied("Archived projects cannot be modified.")
        if allow_archived_change:
            setattr(self, "_allow_archived_write", True)
        super().save(*args, **kwargs)
        if allow_archived_change and hasattr(self, "_allow_archived_write"):
            delattr(self, "_allow_archived_write")

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.org.slug}:{self.slug}"

    @property
    def is_archived(self) -> bool:
        return self.status == Project.Status.ARCHIVED

    def sync_role_memberships(self):
        """Ensure ProjectMember rows reflect the project-level role assignments."""
        if self.status == Project.Status.ARCHIVED:
            return
        special_roles = {
            ProjectMember.Role.PROJECT_MANAGER,
            ProjectMember.Role.SUPERINTENDENT,
            ProjectMember.Role.QUALITY_MANAGER,
            ProjectMember.Role.QUALITY_TECH,
        }

        role_users: dict[str, set[int]] = {
            ProjectMember.Role.PROJECT_MANAGER: set(
                [self.project_manager_id] if self.project_manager_id else []
            ),
            ProjectMember.Role.SUPERINTENDENT: set(
                [self.superintendent_id] if self.superintendent_id else []
            ),
            ProjectMember.Role.QUALITY_MANAGER: set(
                [self.quality_manager_id] if self.quality_manager_id else []
            ),
            ProjectMember.Role.QUALITY_TECH: set(
                self.quality_techs.values_list("id", flat=True)
            ),
        }

        def _ensure_membership(role: str, user_ids: Iterable[int]):
            for user_id in user_ids:
                if not user_id:
                    continue
                ProjectMember.objects.update_or_create(
                    project=self,
                    user_id=user_id,
                    defaults={
                        "role": role,
                        "added_by": self.updated_by or self.created_by,
                    },
                )

        for role, user_ids in role_users.items():
            _ensure_membership(role, user_ids)

        # Downgrade or clean up members no longer assigned to specific roles.
        memberships = ProjectMember.objects.filter(project=self)
        with transaction.atomic():
            for membership in memberships.select_for_update():
                desired_role = None
                if membership.user_id in role_users[ProjectMember.Role.PROJECT_MANAGER]:
                    desired_role = ProjectMember.Role.PROJECT_MANAGER
                elif membership.user_id in role_users[ProjectMember.Role.SUPERINTENDENT]:
                    desired_role = ProjectMember.Role.SUPERINTENDENT
                elif membership.user_id in role_users[ProjectMember.Role.QUALITY_MANAGER]:
                    desired_role = ProjectMember.Role.QUALITY_MANAGER
                elif membership.user_id in role_users[ProjectMember.Role.QUALITY_TECH]:
                    desired_role = ProjectMember.Role.QUALITY_TECH

                if desired_role and membership.role != desired_role:
                    membership.role = desired_role
                    membership.save(update_fields=["role"])
                elif not desired_role and membership.role in special_roles:
                    membership.role = ProjectMember.Role.MEMBER
                    membership.save(update_fields=["role"])


class ProjectMember(models.Model):
    class Role(models.TextChoices):
        PROJECT_MANAGER = "PROJECT_MANAGER", "Project Manager"
        SUPERINTENDENT = "SUPERINTENDENT", "Superintendent"
        QUALITY_MANAGER = "QUALITY_MANAGER", "Quality Manager"
        QUALITY_TECH = "QUALITY_TECH", "Quality Tech"
        MEMBER = "MEMBER", "Member"
        GUEST = "GUEST", "Guest"  # read-only

        @classmethod
        def managerial_roles(cls) -> set[str]:
            return {
                cls.PROJECT_MANAGER,
                cls.SUPERINTENDENT,
                cls.QUALITY_MANAGER,
            }

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_memberships",
    )
    role = models.CharField(
        max_length=32,
        choices=Role.choices,
        default=Role.MEMBER,
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("project", "user")]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.user} in {self.project} ({self.role})"
