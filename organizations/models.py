from django.db import models
from django.db.models import Q
from django.conf import settings
from django.utils.text import slugify
import secrets
from django.utils import timezone

def generate_token():
    return secrets.token_urlsafe(32)

class Organization(models.Model):
    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=80, unique=True, db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_orgs"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:60]
            slug = base
            i = 1
            while Organization.objects.filter(slug=slug).exists():
                i += 1
                slug = f"{base}-{i}"
            self.slug = slug
        if self.slug:
            self.slug = self.slug.lower()

        super().save(*args, **kwargs)

    def __str__(self): return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER  = "OWNER",  "Owner"
        ADMIN  = "ADMIN",  "Admin"
        MEMBER = "MEMBER", "Member"
        VIEWER = "VIEWER", "Viewer"
        GUEST  = "GUEST",  "Guest"   # read-only client role

    org  = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("org", "user")]
        constraints = [
            # Enforce: a user can have at most ONE membership where role != GUEST
            # (PostgreSQL partial unique index)
            models.UniqueConstraint(
                fields=["user"],
                condition=~Q(role="GUEST"),
                name="uniq_single_non_guest_membership_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.user} @ {self.org} ({self.role})"

class Invitation(models.Model):
    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    token = models.CharField(
        max_length=64,
        unique=True,
        default=generate_token,
    )
    role = models.CharField(max_length=10, choices=Membership.Role.choices, default=Membership.Role.MEMBER)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="sent_invites")
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def accept(self, user):
        Membership.objects.get_or_create(org=self.org, user=user, defaults={"role": self.role})
        self.accepted_at = timezone.now()
        self.save(update_fields=["accepted_at"])

    def __str__(self): return f"Invite {self.email} to {self.org.name}"
