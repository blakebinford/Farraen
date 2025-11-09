from django.contrib import admin

from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        "org",
        "doc_type",
        "number",
        "get_version",
        "title",
        "uploaded_by",
        "created_at",
    )
    list_filter = ("org", "doc_type")
    search_fields = ("number", "title", "latest_version__sha256")

    @admin.display(description="Version")
    def get_version(self, obj):
        return obj.version
