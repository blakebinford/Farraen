from django.core.exceptions import PermissionDenied
from django.db.models.signals import m2m_changed, post_save, pre_delete, pre_save
from django.dispatch import receiver
from .models import Project
from drive.models import Folder

STANDARD = [
    "NDE Report",
    "Inspector Qualification",
    "Welder Qualification",
    "MTR",
    "WPS",
    "Calibration",
]

@receiver(post_save, sender=Project)
def seed_drive_folders(sender, instance: Project, created, raw, **kwargs):
    if raw or not created:
        return
    for name in STANDARD:
        Folder.objects.create(
            org=instance.org,
            project=instance,
            name=name,
            parent=None,
            created_by=instance.created_by,
        )


def _get_project(instance):
    project = getattr(instance, "project", None)
    if isinstance(project, Project):
        return project
    project_id = getattr(instance, "project_id", None)
    if project_id:
        return Project.objects.filter(pk=project_id).only("status").first()
    return None


@receiver(pre_save)
def block_archived_project_writes(sender, instance, raw=False, **kwargs):
    if raw:
        return
    if isinstance(instance, Project):
        if getattr(instance, "_allow_archived_write", False):
            return
        if instance.pk:
            previous = Project.objects.filter(pk=instance.pk).only("status").first()
            if previous and previous.status == Project.Status.ARCHIVED and instance.status == Project.Status.ARCHIVED:
                raise PermissionDenied("This project is archived and locked.")
        return
    project = _get_project(instance)
    if not project or getattr(instance, "_allow_archived_write", False):
        return
    if project.status == Project.Status.ARCHIVED:
        raise PermissionDenied("This project is archived and locked.")


@receiver(pre_delete)
def block_archived_project_delete(sender, instance, using, **kwargs):
    project = _get_project(instance)
    if not project or getattr(instance, "_allow_archived_write", False):
        return
    if project.status == Project.Status.ARCHIVED:
        raise PermissionDenied("This project is archived and locked.")


@receiver(post_save, sender=Project)
def sync_roles_on_project_save(sender, instance: Project, created, raw, **kwargs):
    if raw:
        return
    instance.sync_role_memberships()


@receiver(m2m_changed, sender=Project.quality_techs.through)
def sync_roles_on_quality_techs(sender, instance: Project, action, **kwargs):
    if action in {"post_add", "post_remove", "post_clear"}:
        instance.sync_role_memberships()
