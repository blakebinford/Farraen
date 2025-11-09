from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("drive", "0006_fileversion_note"),
    ]

    operations = [
        migrations.AddField(
            model_name="filenode",
            name="doc_type",
            field=models.CharField(blank=True, choices=[
                ("NDE", "NDE Report"),
                ("INSPECTOR", "Inspector Qualification"),
                ("WELDER", "Welder Qualification"),
                ("MTR", "MTR"),
                ("WPS", "WPS"),
                ("CALIBRATION", "Calibration"),
            ], default="", max_length=16),
        ),
        migrations.AddField(
            model_name="filenode",
            name="number",
            field=models.CharField(blank=True, db_index=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="filenode",
            name="title",
            field=models.CharField(blank=True, default="", max_length=256),
        ),
    ]
