from django.db import models
from django.conf import settings
import hashlib

class Document(models.Model):
    DOC_TYPES = [
        ("DRAWING", "Drawing"),
        ("MTR", "Material Test Report"),
        ("WPS", "Welding Procedure Spec"),
        ("NDE", "NDE Report"),
        ("COATING", "Coating Report"),
        ("TORQUE", "Torque Report"),
        ("OTHER", "Other"),
    ]
    org         = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="documents")
    doc_type    = models.CharField(max_length=16, choices=DOC_TYPES)
    number      = models.CharField(max_length=128, db_index=True)        # e.g., D-1002, HEAT-1234, WPS-001
    title       = models.CharField(max_length=256, blank=True)
    file        = models.FileField(upload_to="docs/%Y/%m/")
    version     = models.PositiveIntegerField(default=1)
    sha256      = models.CharField(max_length=64, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("org", "doc_type", "number", "version")]
        ordering = ["doc_type", "number", "-version"]

    def __str__(self):
        return f"{self.doc_type}:{self.number} v{self.version}"

    def compute_sha256(self, content) -> str:
        h = hashlib.sha256()
        for chunk in content.chunks() if hasattr(content, "chunks") else [content.read()]:
            h.update(chunk)
        return h.hexdigest()
