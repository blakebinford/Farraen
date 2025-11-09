from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("drive", "0007_filenode_document_fields"),
        ("welds", "0002_remove_weld_heat_number_remove_weld_joint_type_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="materialheat",
            name="mtr_document",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="material_heats",
                to="drive.filenode",
                limit_choices_to={"doc_type": "MTR"},
            ),
        ),
        migrations.AlterField(
            model_name="materialheat",
            name="wps_document",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="material_heat_wps",
                to="drive.filenode",
                limit_choices_to={"doc_type": "WPS"},
            ),
        ),
    ]
