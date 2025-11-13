from django.db import migrations


def _refresh_descendants(folder):
    for child in folder.children.all():
        child.save()
        _refresh_descendants(child)


def flatten_project_roots(apps, schema_editor):
    Folder = apps.get_model("drive", "Folder")

    root_folders = (
        Folder.objects.filter(parent__isnull=True, project__isnull=False)
        .select_related("project")
        .prefetch_related("children")
    )

    for root in root_folders:
        project = root.project
        if not project:
            continue
        if root.name != project.name:
            continue

        children = list(root.children.all())
        for child in children:
            child.parent = None
            child.save()
            _refresh_descendants(child)

        if not root.is_archived:
            root.is_archived = True
            root.save(update_fields=["is_archived"])


def reverse_flatten(apps, schema_editor):
    # No-op reverse; we can't reliably recreate archived project roots
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("drive", "0007_filenode_document_fields"),
    ]

    operations = [
        migrations.RunPython(flatten_project_roots, reverse_flatten),
    ]
