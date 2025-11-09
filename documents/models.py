from django.db import models, transaction

from drive.models import FileNode, Folder, FileVersion


class DocumentQuerySet(models.QuerySet):
    def documents(self):
        return self.exclude(doc_type="")


class DocumentManager(models.Manager.from_queryset(DocumentQuerySet)):
    def get_queryset(self):
        return super().get_queryset().exclude(doc_type="")

    def create_document(
        self,
        *,
        org,
        doc_type,
        number,
        title="",
        name=None,
        file,
        uploaded_by=None,
        project=None,
        folder=None,
    ):
        if not file:
            raise ValueError("A file is required when creating a document.")

        folder = folder or Document.default_folder(org)

        display_name = (name or getattr(file, "name", "")).strip()
        if not display_name:
            raise ValueError("A name is required when creating a document.")

        with transaction.atomic():
            node = (
                self.select_for_update()
                .filter(org=org, doc_type=doc_type, number=number)
                .first()
            )

            if node is None:
                node = self.model(
                    org=org,
                    project=project,
                    folder=folder,
                    doc_type=doc_type,
                    number=number,
                    title=title or display_name,
                    name=display_name,
                    created_by=uploaded_by,
                )
                node.save(using=self._db)
            else:
                updates = []
                if title and title != node.title:
                    node.title = title
                    updates.append("title")
                if folder and node.folder_id != folder.id:
                    node.folder = folder
                    updates.append("folder")
                if display_name and node.name != display_name:
                    node.name = display_name
                    updates.append("name")
                if updates:
                    node.save(update_fields=updates)

            current_version = node.version
            FileVersion.objects.using(self._db).create(
                file_node=node,
                version=current_version + 1 if current_version else 1,
                blob=file,
                uploaded_by=uploaded_by,
                content_type=getattr(file, "content_type", ""),
            )

        node.refresh_from_db()
        return node

    def create(self, **kwargs):
        file = kwargs.pop("file", None)
        return self.create_document(file=file, **kwargs)


class Document(FileNode):
    objects = DocumentManager()
    DocType = FileNode.DocType

    class Meta:
        proxy = True
        ordering = ["doc_type", "number", "-created_at"]

    def __str__(self):
        return f"{self.doc_type}:{self.number} v{self.version}"

    @staticmethod
    def default_folder(org):
        folder, _ = Folder.objects.get_or_create(
            org=org,
            parent=None,
            slug="documents",
            defaults={"name": "Documents"},
        )
        return folder
