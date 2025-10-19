import hashlib
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Document

@receiver(post_save, sender=Document)
def compute_sha256_after_save(sender, instance: Document, created, **kwargs):
    """
    Compute the file hash *after* Django has saved the file to storage.
    Avoid recursion by using .update() instead of instance.save().
    Only compute if sha256 is blank and file exists.
    """
    if not instance.file or instance.sha256:
        return

    # Open from storage; this will not interfere with upload pipeline.
    with instance.file.open("rb") as f:
        data = f.read()
    sha = hashlib.sha256(data).hexdigest()

    # Update the row without triggering signals again
    Document.objects.filter(pk=instance.pk).update(sha256=sha)
