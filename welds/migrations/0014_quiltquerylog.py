from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("organizations", "0001_initial"),
        ("projects", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("welds", "0013_materialheatdraft"),
    ]

    operations = [
        migrations.CreateModel(
            name="QuiltQueryLog",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("intent", models.CharField(max_length=64)),
                ("filters", models.JSONField(blank=True, default=dict)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "org",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="quilt_queries",
                        to="organizations.organization",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.CASCADE,
                        related_name="quilt_queries",
                        to="projects.project",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="quilt_queries",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
