from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0002_alter_document_doc_type"),
        ("drive", "0007_filenode_document_fields"),
        ("welds", "0003_materialheat_documents_to_filenode"),
    ]

    operations = [
        migrations.DeleteModel(
            name="Document",
        ),
    ]
