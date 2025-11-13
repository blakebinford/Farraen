import logging
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Count, Max, Sum
from django.utils import timezone

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
    class WeldLengthSource(models.TextChoices):
        MEASURED = "MEASURED", "Measured"
        STITCH = "STITCH", "Derived from stitch length"
        STENCIL_AVERAGE = "STENCIL_AVG", "Stencil average"
        PROJECT_AVERAGE = "PROJECT_AVG", "Project average"
        OUTER_DIAMETER = "OD_PI", "Outer diameter circumference"
        MANUAL = "MANUAL", "Manual entry"

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
    primary_welder = models.ForeignKey(
        "welds.Welder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="primary_welds",
    )
    primary_stencil = models.CharField(max_length=64, blank=True)
    weld_length_inches = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Canonical measured length of the weld in inches.",
    )
    weld_length_source = models.CharField(
        max_length=32,
        choices=WeldLengthSource.choices,
        blank=True,
        help_text="Source used to derive the weld length value.",
    )
    weld_length_basis = models.CharField(
        max_length=255,
        blank=True,
        help_text="Additional notes describing how weld length was derived.",
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
    weld_date = models.DateField(
        null=True,
        blank=True,
        help_text="Normalized weld date used for production reporting.",
    )
    date_welded = models.DateField(null=True, blank=True)
    heat_number = models.CharField(max_length=128, blank=True)
    pipe_size = models.CharField(max_length=64, blank=True)
    od = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Outer diameter in inches.",
    )
    wps_document = models.ForeignKey(
        FileNode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="welds",
        limit_choices_to={"doc_type": FileNode.DocType.WPS},
    )
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
        indexes = [
            models.Index(fields=["project", "weld_length_inches"]),
            models.Index(fields=["project", "weld_date"]),
            models.Index(fields=["heat_number"]),
            models.Index(fields=["pipe_size"]),
            models.Index(fields=["od"]),
            models.Index(fields=["wps_document"]),
            models.Index(fields=["primary_welder"]),
            models.Index(fields=["primary_stencil"]),
        ]

    def __str__(self) -> str:
        return f"{self.project}::{self.weld_id}"

    def save(self, *args, **kwargs):
        if self.weld_date is None and self.date_welded is not None:
            self.weld_date = self.date_welded
        if not self.primary_stencil and self.primary_welder_id:
            self.primary_stencil = self.primary_welder.stencil
        if not self.heat_number and self.material1_heat_id:
            self.heat_number = self.material1_heat.heat_number
        if not self.pipe_size and self.material1_description:
            self.pipe_size = self.material1_description
        if self.od is None:
            outer_diameter = self._select_outer_diameter()
            if outer_diameter is not None:
                self.od = outer_diameter
        if not self.wps_document and self.material1_heat_id:
            self.wps_document = self.material1_heat.wps_document
        super().save(*args, **kwargs)

    def _select_outer_diameter(self):
        candidates = [
            self.material1_outer_diameter_in,
            getattr(self.material1_heat, "outer_diameter_in", None)
            if self.material1_heat_id
            else None,
            self.material2_outer_diameter_in,
            getattr(self.material2_heat, "outer_diameter_in", None)
            if self.material2_heat_id
            else None,
        ]
        for value in candidates:
            if value is not None:
                return value
        return None

    @property
    def weld_inches(self) -> Decimal:
        if self.weld_length_inches is not None:
            return Decimal(self.weld_length_inches)
        outer_diameter = self._select_outer_diameter()
        if outer_diameter is not None:
            return outer_diameter * Decimal("3.14")
        return Decimal("0")


class WeldInspection(models.Model):
    class Result(models.TextChoices):
        ACCEPTED = "ACCEPTED", "Accepted"
        REPAIR = "REPAIR", "Requires Repair"
        REJECTED = "REJECTED", "Rejected"

    weld = models.ForeignKey(
        "Weld",
        on_delete=models.CASCADE,
        related_name="inspections",
    )
    inspection_date = models.DateField(null=True, blank=True)
    nde_type = models.CharField(max_length=32, blank=True)
    nde_rig = models.CharField(max_length=128, blank=True)
    report_reference = models.CharField(max_length=255, blank=True)
    result = models.CharField(
        max_length=16,
        choices=Result.choices,
        default=Result.ACCEPTED,
    )
    requires_repair = models.BooleanField(default=False)
    defect_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-inspection_date", "-id"]
        indexes = [
            models.Index(fields=["weld", "inspection_date"]),
            models.Index(fields=["result"]),
        ]

    def __str__(self) -> str:
        weld_id = getattr(self.weld, "weld_id", "#")
        return f"Inspection {self.pk} for {weld_id}"


class WeldRepair(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        CLOSED = "CLOSED", "Closed"

    weld = models.ForeignKey(
        "Weld",
        on_delete=models.CASCADE,
        related_name="repairs",
    )
    repair_sequence = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.OPEN,
    )
    flagged_at = models.DateField(null=True, blank=True)
    original_ndereport_ref = models.CharField(max_length=255, null=True, blank=True)
    original_nderig = models.CharField(max_length=128, null=True, blank=True)
    defect_code_snapshot = models.CharField(max_length=64, null=True, blank=True)
    nde_type = models.CharField(max_length=32, null=True, blank=True)
    manual_flag = models.BooleanField(default=False)
    repair_date = models.DateField(null=True, blank=True)
    repair_stencil = models.CharField(max_length=32, null=True, blank=True)
    reinspection_nderig = models.CharField(max_length=128, null=True, blank=True)
    reinspection_result = models.CharField(max_length=16, null=True, blank=True)
    reinspection_passed_at = models.DateField(null=True, blank=True)
    last_reinspection_report_ref = models.CharField(max_length=255, null=True, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_repairs",
    )
    comments = models.TextField(null=True, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    total_inches_repaired = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    last_attempt_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_repairs",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_repairs",
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="closed_repairs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-flagged_at", "-id"]
        indexes = [
            models.Index(fields=["weld", "status"]),
            models.Index(fields=["status"]),
            models.Index(fields=["flagged_at"]),
            models.Index(fields=["reinspection_passed_at"]),
        ]

    def __str__(self) -> str:
        weld_id = getattr(self.weld, "weld_id", "#")
        return f"Repair {self.pk} ({weld_id})"

    @property
    def weld_id_display(self) -> str:
        return getattr(self.weld, "weld_id", "#")

    @property
    def is_closed(self) -> bool:
        return self.status == self.Status.CLOSED

    def refresh_attempt_metrics(self):
        aggregates = self.attempts.aggregate(
            count=Count("id"),
            total=Sum("inches_repaired"),
            last=Max("performed_at"),
        )
        attempt_count = aggregates.get("count") or 0
        total_inches = aggregates.get("total") or Decimal("0.00")
        last_attempt = aggregates.get("last")
        update_fields = [
            "attempt_count",
            "total_inches_repaired",
            "last_attempt_date",
            "repair_date",
        ]
        self.attempt_count = attempt_count
        self.total_inches_repaired = total_inches
        self.last_attempt_date = last_attempt
        if last_attempt:
            self.repair_date = last_attempt
        self.save(update_fields=update_fields)

    def refresh_reinspection_state(self):
        latest = self.reinspections.order_by("-reinspection_date", "-id").first()
        if not latest:
            return
        self.reinspection_nderig = latest.reinspection_nderig
        self.reinspection_result = latest.reinspection_result
        self.last_reinspection_report_ref = latest.reinspection_ndereport_ref
        if latest.reinspection_result == Reinspection.Result.PASS:
            self.status = self.Status.CLOSED
            self.reinspection_passed_at = latest.reinspection_date
            if latest.created_by_id and not self.closed_by_id:
                self.closed_by_id = latest.created_by_id
            if not self.closed_at:
                self.closed_at = timezone.now()
        self.save(
            update_fields=[
                "reinspection_nderig",
                "reinspection_result",
                "last_reinspection_report_ref",
                "status",
                "reinspection_passed_at",
                "closed_by",
                "closed_at",
            ]
        )

    def close(self, *, closed_by=None, closed_at=None, result="PASS"):
        if self.is_closed:
            if closed_by and not self.closed_by_id:
                self.closed_by = closed_by
                self.save(update_fields=["closed_by"])
            return
        self.status = self.Status.CLOSED
        self.reinspection_result = result
        self.reinspection_passed_at = self.reinspection_passed_at or (
            closed_at.date() if isinstance(closed_at, timezone.datetime) else closed_at
        )
        self.closed_at = closed_at or timezone.now()
        if closed_by:
            self.closed_by = closed_by
        self.save(
            update_fields=[
                "status",
                "reinspection_result",
                "reinspection_passed_at",
                "closed_at",
                "closed_by",
            ]
        )


class RepairAttempt(models.Model):
    class Outcome(models.TextChoices):
        PASS = "PASS", "Pass"
        FAIL = "FAIL", "Fail"
        CANCELLED = "CANCELLED", "Cancelled"

    repair = models.ForeignKey(
        WeldRepair,
        on_delete=models.CASCADE,
        related_name="attempts",
    )
    attempt_no = models.PositiveIntegerField(blank=True, null=True)
    performed_at = models.DateField()
    wps_document = models.ForeignKey(
        FileNode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="repair_attempts",
        limit_choices_to={"doc_type": FileNode.DocType.WPS},
    )
    welder_stencil = models.CharField(max_length=64)
    inches_repaired = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="repair_attempts",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["attempt_no", "id"]
        indexes = [
            models.Index(fields=["repair", "attempt_no"]),
        ]

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if is_new and not self.attempt_no:
            last_attempt = (
                type(self)
                .objects.filter(repair=self.repair)
                .order_by("-attempt_no")
                .values_list("attempt_no", flat=True)
                .first()
            )
            self.attempt_no = (last_attempt or 0) + 1
        super().save(*args, **kwargs)
        if is_new:
            self.repair.refresh_attempt_metrics()


class Reinspection(models.Model):
    class Result(models.TextChoices):
        PASS = "PASS", "Pass"
        FAIL = "FAIL", "Fail"
        HOLD = "HOLD", "Hold"

    repair = models.ForeignKey(
        WeldRepair,
        on_delete=models.CASCADE,
        related_name="reinspections",
    )
    reinspection_date = models.DateField()
    reinspection_nderig = models.CharField(max_length=128)
    reinspection_ndereport_ref = models.CharField(max_length=255)
    reinspection_result = models.CharField(max_length=16, choices=Result.choices)
    inspector = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="repair_reinspections",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-reinspection_date", "-id"]
        indexes = [
            models.Index(fields=["repair", "reinspection_date"]),
            models.Index(fields=["reinspection_result"]),
        ]

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new:
            self.repair.refresh_reinspection_state()


class WeldHistory(models.Model):
    class ChangeType(models.TextChoices):
        CREATE = "CREATE", "Create"
        UPDATE = "UPDATE", "Update"
        ROLLBACK = "ROLLBACK", "Rollback"

    weld = models.ForeignKey(
        "Weld",
        on_delete=models.CASCADE,
        related_name="history",
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="weld_history_changes",
    )
    change_type = models.CharField(
        max_length=16,
        choices=ChangeType.choices,
    )
    changed_fields = models.JSONField(default=list)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["weld", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.weld_id_display} – {self.change_type}"

    @property
    def weld_id_display(self) -> str:
        return getattr(self.weld, "weld_id", "#")


class WeldEvent(models.Model):
    class Action(models.TextChoices):
        CREATE = "CREATE", "Create"
        UPDATE = "UPDATE", "Update"
        DELETE = "DELETE", "Delete"

    weld = models.ForeignKey(
        "Weld",
        on_delete=models.CASCADE,
        related_name="events",
    )
    action = models.CharField(max_length=16, choices=Action.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="weld_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    changes = models.JSONField(default=dict)
    ip_address = models.CharField(max_length=45, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["weld", "-created_at"]),
        ]

    def __str__(self) -> str:
        weld_id = getattr(self.weld, "weld_id", "#")
        timestamp = timezone.localtime(self.created_at) if self.created_at else None
        formatted_ts = timestamp.strftime("%Y-%m-%d %H:%M:%S") if timestamp else ""
        return f"{weld_id} – {self.action} {formatted_ts}".strip()
