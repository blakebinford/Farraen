from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from .models import Membership

@receiver(post_save, sender=Membership)
def ensure_primary_org(sender, instance: Membership, created, **kwargs):
    user = instance.user
    if instance.role != Membership.Role.GUEST:
        # Set primary_org if missing or different
        if user.primary_org_id != instance.org_id:
            user.primary_org = instance.org
            user.save(update_fields=["primary_org"])
