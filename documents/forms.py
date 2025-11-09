from django import forms

from .models import Document


class DocumentForm(forms.ModelForm):
    file = forms.FileField()

    class Meta:
        model = Document
        fields = ["doc_type", "number", "title", "file"]

    def save(self, *, org, user):
        cleaned = self.cleaned_data
        return Document.objects.create_document(
            org=org,
            doc_type=cleaned["doc_type"],
            number=cleaned["number"],
            title=cleaned.get("title", ""),
            file=cleaned["file"],
            uploaded_by=user,
        )
