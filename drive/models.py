from django.db import models
from django.conf import settings
from django.utils.timezone import now
from django.utils import timezone
from django.utils.text import slugify


class Folder(models.Model):
    org         = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="folders")
    project     = models.ForeignKey("projects.Project", null=True, blank=True, on_delete=models.CASCADE,
                                related_name="folders")
    name        = models.CharField(max_length=255)
    slug        = models.SlugField(max_length=255, db_index=True)
    parent      = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")
    path        = models.CharField(max_length=1024, db_index=True)   # e.g. /Projects/ABC/Drawings
    depth       = models.PositiveIntegerField(default=0)
    is_archived = models.BooleanField(default=False)
    created_by  = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("org", "parent", "slug")]
        ordering = ["path"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:255] or "folder"
        if self.parent:
            self.path = f"{self.parent.path}/{self.slug}".replace("//","/")
            self.depth = self.parent.depth + 1
        else:
            self.path = f"/{self.slug}"
            self.depth = 0
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.org.slug}:{self.path}"


class FileNode(models.Model):
    """A logical file (name in a folder) pointing to its latest version."""
    org            = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="files")
    project        = models.ForeignKey("projects.Project", null=True, blank=True, on_delete=models.CASCADE,
                                related_name="files")
    folder         = models.ForeignKey(Folder, on_delete=models.PROTECT, related_name="files")
    name           = models.CharField(max_length=255)                       # e.g. D-201.pdf
    slug           = models.SlugField(max_length=255)
    content_type   = models.CharField(max_length=150, blank=True)
    size           = models.BigIntegerField(default=0)
    latest_version = models.ForeignKey("FileVersion", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    is_locked      = models.BooleanField(default=False)
    locked_by      = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    locked_at      = models.DateTimeField(null=True, blank=True)
    is_archived    = models.BooleanField(default=False)
    created_by     = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at     = models.DateTimeField(auto_now_add=True)
    checked_out_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="checked_out_files",
    )
    checked_out_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("org", "folder", "slug")]
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            base = self.name.rsplit("/",1)[-1]
            self.slug = slugify(base)[:255] or "file"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.folder.path}/{self.name}"


def upload_to(instance, filename):
    dt = now()  # created_at not set yet on first save
    # file_node_id is available because you pass file_node when creating FileVersion
    node_id = instance.file_node_id or (getattr(instance.file_node, "id", "tmp"))
    org_slug = instance.file_node.org.slug if instance.file_node and instance.file_node.org else "org"
    ver = instance.version or 1
    # MEDIA_ROOT/drive/<org>/<yyyy>/<mm>/<nodeId>_<version>_<filename>
    return f"drive/{org_slug}/{dt:%Y}/{dt:%m}/{node_id}_{ver}_{filename}"

class FileVersion(models.Model):
    """Immutable content blob (one row per version)."""
    file_node   = models.ForeignKey(FileNode, on_delete=models.CASCADE, related_name="versions")
    version     = models.PositiveIntegerField()                             # 1,2,3...
    blob        = models.FileField(upload_to=upload_to)
    sha256      = models.CharField(max_length=64, blank=True)
    size        = models.BigIntegerField(default=0)
    content_type= models.CharField(max_length=150, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at  = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        unique_together = [("file_node", "version")]
        ordering = ["-version"]

    def __str__(self):
        return f"{self.file_node} v{self.version}"

class FileEvent(models.Model):
    class Action(models.TextChoices):
        VIEW = "VIEW", "View"
        PREVIEW = "PREVIEW", "Preview"
        DOWNLOAD = "DOWNLOAD", "Download"
        UPLOAD = "UPLOAD", "Upload"
        CHECKOUT = "CHECKOUT", "Check-out"
        CHECKIN = "CHECKIN", "Check-in"
        FORCE_CHECKIN = "FORCE_CHECKIN", "Force check-in"

    org        = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="file_events")
    file_node  = models.ForeignKey(FileNode, on_delete=models.CASCADE, related_name="events")
    version    = models.ForeignKey(FileVersion, null=True, blank=True, on_delete=models.SET_NULL, related_name="events")
    actor      = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="file_events")
    action     = models.CharField(max_length=16, choices=Action.choices)
    ip         = models.GenericIPAddressField(null=True, blank=True)
    ua         = models.CharField(max_length=512, blank=True)
    at         = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["file_node", "-at"], name="fe_node_at_desc"),
            models.Index(fields=["org", "-at"], name="fe_org_at_desc"),
            models.Index(fields=["action", "-at"], name="fe_action_at"),
        ]
        ordering = ["-at"]

    def __str__(self):
        who = self.actor_id or "system"
        ver = f" v{self.version_id}" if self.version_id else ""
        return f"{self.action} {self.file_node_id}{ver} by {who} @ {self.at.isoformat()}"