from django.db import models
from django.conf import settings
from django.utils.text import slugify

class Project(models.Model):
    org = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="projects")
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, db_index=True)
    is_archived = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("org", "slug")]
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:200] or "project"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.org.slug}:{self.slug}"


class ProjectMember(models.Model):
    class Role(models.TextChoices):
        MANAGER = "MANAGER", "Manager"
        MEMBER  = "MEMBER",  "Member"
        GUEST   = "GUEST",   "Guest"  # read-only

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="project_memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("project", "user")]

    def __str__(self):
        return f"{self.user} in {self.project} ({self.role})"
