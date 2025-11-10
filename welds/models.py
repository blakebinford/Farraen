import logging

from django.conf import settings
from django.db import models

from drive.models import FileNode, Folder


logger = logging.getLogger(__name__)


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
        "drive.FileNode",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="material_heats",
        limit_choices_to={"doc_type": "MTR"},
    )
    wps_document = models.ForeignKey(
        "drive.FileNode",
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


class Welder(models.Model):
    org = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="welders",
    )
    name = models.CharField(max_length=255)
    stencil = models.CharField(max_length=50)
    employee_id = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    approved_wps = models.ManyToManyField(
        FileNode,
        blank=True,
        related_name="approved_welders",
        limit_choices_to={"doc_type": FileNode.DocType.WPS},
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["stencil", "name"]
        unique_together = [("org", "stencil")]

    def __str__(self) -> str:
        return f"{self.stencil} – {self.name}"


class NDERig(models.Model):
    org = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="nde_rigs",
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="nde_rigs",
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    qualification_folder = models.OneToOneField(
        Folder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nde_rig",
    )

    class Meta:
        ordering = ["name"]
        unique_together = [("org", "project", "name")]

    def __str__(self) -> str:
        return f"{self.name} ({self.org.slug})"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new and self.project_id and not self.qualification_folder_id:
            folder = self._ensure_qualification_folder()
            if folder:
                type(self).objects.filter(pk=self.pk).update(
                    qualification_folder=folder
                )
                self.qualification_folder = folder

    def _ensure_qualification_folder(self):
        if not self.project_id:
            return None
        inspector_root = (
            Folder.objects.filter(
                org=self.org,
                project=self.project,
                name="Inspector Qualification",
            )
            .order_by("depth")
            .first()
        )
        if not inspector_root:
            logger.warning(
                "Inspector Qualification folder missing when creating NDE rig",
                extra={
                    "org_id": self.org_id,
                    "project_id": self.project_id,
                    "nde_rig_name": self.name,
                },
            )
            return None
        existing = Folder.objects.filter(
            org=self.org,
            project=self.project,
            parent=inspector_root,
            name=self.name,
        ).first()
        if existing:
            return existing
        return Folder.objects.create(
            org=self.org,
            project=self.project,
            parent=inspector_root,
            name=self.name,
        )


class Weld(models.Model):
    class WeldType(models.TextChoices):
        GENERIC = "GENERIC", "Basic / Generic"
        BUTT = "BUTT", "Butt weld"
        FILLET = "FILLET", "Fillet weld"
        SOCKET = "SOCKET", "Socket weld"
        BRANCH = "BRANCH", "Branch / Tie-In"
        OVERLAY = "OVERLAY", "Overlay / Build-Up"
        OTHER = "OTHER", "Other"

    class RepairType(models.TextChoices):
        CRACK = "CRACK", "Crack"
        POROSITY = "POROSITY", "Porosity"
        SLAG = "SLAG", "Slag"
        LACK_OF_FUSION = "LOF", "Lack of fusion"
        UNDERCUT = "UNDERCUT", "Undercut"
        INCOMPLETE_PENETRATION = "INCOMPLETE_PEN", "Incomplete penetration"
        ARC_STRIKES_OTHER = "OTHER", "Arc strikes / Other"

    class NDEType(models.TextChoices):
        RADIOGRAPHIC = "RT", "RT (Radiographic Testing)"
        MAGNETIC_PARTICLE = "MT", "MT (Magnetic Particle Testing)"
        PENETRANT = "PT", "PT (Penetrant Testing)"
        ULTRASONIC = "UT", "UT (Ultrasonic Testing)"
        PHASED_ARRAY = "PA", "PA (Phased Array UT)"
        VISUAL = "VT", "VT (Visual Testing)"
        OTHER = "OTHER", "Other"

    class Disposition(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACCEPTED = "ACCEPTED", "Accepted"
        REPAIR = "REPAIR", "Repair"
        CUT_OUT = "CUT_OUT", "Cut Out"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="welds",
    )
    weld_id = models.CharField(max_length=100)
    nde_number = models.CharField(max_length=120, blank=True)
    nde_type = models.CharField(
        max_length=16,
        choices=NDEType.choices,
        blank=True,
        null=True,
        verbose_name="NDE type",
        help_text="NDE method used for this weld",
    )
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
    weld_type = models.CharField(
        max_length=16,
        choices=WeldType.choices,
        blank=True,
        verbose_name="weld type",
        help_text="Primary weld type for this joint",
    )
    date_welded = models.DateField(null=True, blank=True)
    welder_stencil_root_hotpass = models.CharField(max_length=255, blank=True)
    welder_stencil_fill = models.CharField(max_length=255, blank=True)
    welder_stencil_cap = models.CharField(max_length=255, blank=True)
    welder_stencil_repair = models.CharField(max_length=255, blank=True)
    nde_date = models.DateField(null=True, blank=True)
    nde_rig = models.ForeignKey(
        NDERig,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="welds",
    )
    repair_type = models.CharField(
        max_length=32,
        choices=RepairType.choices,
        blank=True,
        null=True,
        verbose_name="repair type",
        help_text="Type of discontinuity being repaired",
    )
    disposition = models.CharField(
        max_length=20,
        choices=Disposition.choices,
        default=Disposition.PENDING,
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
