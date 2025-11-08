from django.conf import settings
from django.db import models


class MaterialHeat(models.Model):
    org = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="material_heats",
    )
    heat_number = models.CharField(max_length=128)
    description = models.CharField(max_length=255, blank=True)
    material_grade = models.CharField(max_length=120, blank=True)
    outer_diameter_in = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True
    )
    wall_thickness_in = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True
    )
    wps_number = models.CharField(max_length=128, blank=True)
    mtr_document = models.ForeignKey(
        "documents.Document",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="material_heats",
        limit_choices_to={"doc_type": "MTR"},
    )
    wps_document = models.ForeignKey(
        "documents.Document",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="material_heat_wps",
        limit_choices_to={"doc_type": "WPS"},
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["heat_number"]
        unique_together = [("org", "heat_number")]

    def __str__(self) -> str:
        return f"{self.heat_number} ({self.org.slug})"


class NDERig(models.Model):
    org = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="nde_rigs",
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        unique_together = [("org", "name")]

    def __str__(self) -> str:
        return f"{self.name} ({self.org.slug})"


class Weld(models.Model):
    class Disposition(models.TextChoices):
        ACCEPTED = "accepted", "Accepted"
        REPAIR = "repair", "Repair"
        CUT_OUT = "cut_out", "Cut Out"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="welds",
    )
    weld_id = models.CharField(max_length=100)
    nde_number = models.CharField(max_length=120, blank=True)
    drawing_number = models.CharField(max_length=120, blank=True)
    material1_heat = models.ForeignKey(
        MaterialHeat,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="primary_welds",
    )
    material1_description = models.CharField(max_length=255, blank=True)
    material1_grade = models.CharField(max_length=120, blank=True)
    material1_outer_diameter_in = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True
    )
    material1_wall_thickness_in = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True
    )
    material2_heat = models.ForeignKey(
        MaterialHeat,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="secondary_welds",
    )
    material2_description = models.CharField(max_length=255, blank=True)
    material2_grade = models.CharField(max_length=120, blank=True)
    material2_outer_diameter_in = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True
    )
    material2_wall_thickness_in = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True
    )
    weld_type = models.CharField(max_length=120, blank=True)
    date_welded = models.DateField(null=True, blank=True)
    welder_stencil_root_hotpass = models.CharField(max_length=120, blank=True)
    welder_stencil_fill = models.CharField(max_length=120, blank=True)
    welder_stencil_fill_additional = models.CharField(max_length=120, blank=True)
    welder_stencil_cap = models.CharField(max_length=120, blank=True)
    nde_date = models.DateField(null=True, blank=True)
    nde_rig = models.ForeignKey(
        NDERig,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="welds",
    )
    disposition = models.CharField(
        max_length=20,
        choices=Disposition.choices,
        default=Disposition.ACCEPTED,
    )
    disposition_comment = models.TextField(blank=True)

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
