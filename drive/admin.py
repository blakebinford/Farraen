from django.contrib import admin
from .models import Folder, FileNode, FileVersion

@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ("org", "path", "name", "parent", "depth", "is_archived")
    list_filter = ("org","is_archived")
    search_fields = ("path",)

@admin.register(FileNode)
class FileNodeAdmin(admin.ModelAdmin):
    list_display = ("org","folder","name","latest_version","size","is_locked","is_archived")
    list_filter = ("org","is_archived")
    search_fields = ("name","folder__path")

@admin.register(FileVersion)
class FileVersionAdmin(admin.ModelAdmin):
    list_display = ("file_node","version","sha256","size","created_at","uploaded_by")
    search_fields = ("file_node__name","sha256")
