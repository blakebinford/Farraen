from django.contrib import admin
from .models import Document

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("org", "doc_type", "number", "version", "title", "uploaded_by", "created_at")
    list_filter  = ("org", "doc_type")
    search_fields = ("number", "title", "sha256")
