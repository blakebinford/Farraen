from django.db.models.signals import post_save
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
def seed_drive_folders(sender, instance: Project, created, **kwargs):
    if not created:
        return
    # Create project root (no parent)
    root = Folder.objects.create(
        org=instance.org, project=instance,
        name=instance.name, parent=None, created_by=instance.created_by
    )
    # Create standard subfolders
    for name in STANDARD:
        Folder.objects.create(
            org=instance.org, project=instance,
            name=name, parent=root, created_by=instance.created_by
        )
