from django.db import migrations, models
from django.db.models import Q


def copy_welder_stencils(apps, schema_editor):
    Weld = apps.get_model("welds", "Weld")
    db_alias = schema_editor.connection.alias
    queryset = Weld.objects.using(db_alias).select_related("welder").filter(
        welder__isnull=False
    )
    blank_filter = Q(welder_stencil_root_hotpass__isnull=True) | Q(
        welder_stencil_root_hotpass=""
    )
    for weld in queryset.filter(blank_filter):
        stencil = getattr(getattr(weld, "welder", None), "stencil", "")
        if not stencil:
            continue
        weld.welder_stencil_root_hotpass = stencil
        weld.save(update_fields=["welder_stencil_root_hotpass"])


class Migration(migrations.Migration):

    dependencies = [
        ("welds", "0005_alter_nderig_unique_together_nderig_project_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="weld",
            name="welder_stencil_repair",
            field=models.CharField(blank=True, default="", max_length=255),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="weld",
            name="welder_stencil_root_hotpass",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AlterField(
            model_name="weld",
            name="welder_stencil_fill",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AlterField(
            model_name="weld",
            name="welder_stencil_cap",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.RunPython(copy_welder_stencils, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="weld",
            name="welder",
        ),
        migrations.RemoveField(
            model_name="weld",
            name="welder_stencil_fill_additional",
        ),
    ]
