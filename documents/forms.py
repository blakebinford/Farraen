from django import forms

from .models import Document


class DocumentForm(forms.ModelForm):
    name = forms.CharField(max_length=255, required=False)
    file = forms.FileField()

    class Meta:
        model = Document
        fields = ["name", "doc_type", "number", "title", "file"]

    def clean(self):
        cleaned = super().clean()

        name = cleaned.get("name", "").strip()
        title = cleaned.get("title", "").strip()

        if not name:
            upload = self.files.get("file")
            if upload and getattr(upload, "name", ""):
                name = upload.name
                cleaned["name"] = name

        if not title and name:
            cleaned["title"] = name

        return cleaned

    def save(self, *, org, user):
        cleaned = self.cleaned_data
        return Document.objects.create_document(
            org=org,
            doc_type=cleaned["doc_type"],
            number=cleaned["number"],
            title=cleaned.get("title", ""),
            name=cleaned.get("name", ""),
            file=cleaned["file"],
            uploaded_by=user,
        )
