from django.contrib import admin
from .models import Project, ProjectMember
# Register your models here.

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "org", "slug", "is_archived", "created_by", "created_at")
    list_filter = ("org", "is_archived")
    search_fields = ("name", "slug", "org__name", "org__slug", "created_by__email")
    raw_id_fields = ("org", "created_by")
    prepopulated_fields = {"slug": ("name",)}

@admin.register(ProjectMember)
class ProjectMemberAdmin(admin.ModelAdmin):
    list_display = ("project", "user", "role", "added_by", "created_at")
    list_filter = ("role", "project__org", "project")
    search_fields = ("user__email", "user__first_name", "user__last_name", "project__name", "project__slug")
    raw_id_fields = ("project", "user", "added_by")