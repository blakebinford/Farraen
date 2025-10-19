from django.contrib.auth.models import AbstractUser
from django.db import models
from .managers import UserManager

class User(AbstractUser):
    # Make email the unique login; keep username optional/unused
    class Kind(models.TextChoices):
        MEMBER = "MEMBER", "Member"   # standard internal user; belongs to exactly one org
        GUEST  = "GUEST",  "Guest"    # client/guest; may access multiple orgs read-only

    email = models.EmailField(unique=True)
    username = models.CharField(max_length=150, blank=True, null=True, unique=False)

    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.MEMBER)

    # For MEMBER users only. Null for GUESTs.
    primary_org = models.ForeignKey(
        "organizations.Organization",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="primary_users",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email
