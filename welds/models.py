from django.conf import settings
from django.db import models


class Weld(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETE = "complete", "Complete"
        REPAIR = "repair", "Repair"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="welds",
    )
    weld_id = models.CharField(max_length=100)
    joint_type = models.CharField(max_length=120, blank=True)
    location = models.CharField(max_length=255, blank=True)
    wps = models.CharField("WPS", max_length=120, blank=True)
    welder = models.CharField(max_length=120, blank=True)
    heat_number = models.CharField(max_length=120, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PLANNED,
    )
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_welds",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_welds",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["weld_id", "id"]
        unique_together = [("project", "weld_id")]

    def __str__(self) -> str:
        return f"{self.project}::{self.weld_id}"
