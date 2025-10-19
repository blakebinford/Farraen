import hashlib
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import FileVersion, FileNode
from mimetypes import guess_type

@receiver(post_save, sender=FileVersion)
def compute_hash_and_promote(sender, instance, created, **kwargs):
    if not created:
        return

    size = 0
    sha = ""
    if instance.blob:
        with instance.blob.open("rb") as f:
            data = f.read()
        sha = hashlib.sha256(data).hexdigest()
        size = len(data)
        FileVersion.objects.filter(pk=instance.pk).update(sha256=sha, size=size)

    # safest way to determine content type
    ctype = instance.content_type
    if not ctype:
        ctype, _ = guess_type(instance.blob.name)

    FileNode.objects.filter(pk=instance.file_node_id).update(
        latest_version=instance,
        size=size,
        content_type=ctype or "",
    )
