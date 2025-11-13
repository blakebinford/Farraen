from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied

from .models import Project, ProjectMember, Tag


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    search_fields = ("name",)
    list_display = ("name",)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "org",
        "slug",
        "status",
        "project_manager",
        "created_by",
        "created_at",
        "updated_at",
    )
    list_filter = ("org", "status", "project_manager")
    search_fields = (
        "name",
        "slug",
        "project_code",
        "org__name",
        "org__slug",
        "created_by__email",
    )
    raw_id_fields = (
        "org",
        "created_by",
        "updated_by",
        "project_manager",
        "superintendent",
        "quality_manager",
    )
    filter_horizontal = ("quality_techs", "tags")
    prepopulated_fields = {"slug": ("name",)}
    actions = ["archive_projects", "unarchive_projects"]

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj and obj.status == Project.Status.ARCHIVED and not self._can_unarchive(request):
            fields = [field.name for field in obj._meta.fields]
            readonly.extend(fields)
            readonly = list(dict.fromkeys(readonly))
        return readonly

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        allow_archived_change = self._can_unarchive(request)
        if change:
            previous = Project.objects.get(pk=obj.pk)
            if previous.status == Project.Status.ARCHIVED and not allow_archived_change:
                raise PermissionDenied(
                    "Archived projects can only be modified by users who may unarchive them."
                )
            if previous.status == Project.Status.ARCHIVED and allow_archived_change:
                obj.save(allow_archived_change=True)
                form.save_m2m()
                obj.sync_role_memberships()
                return
        super().save_model(request, obj, form, change)
        obj.sync_role_memberships()

    def delete_model(self, request, obj):
        if obj.status == Project.Status.ARCHIVED and not self._can_unarchive(request):
            raise PermissionDenied("Archived projects cannot be deleted.")
        super().delete_model(request, obj)

    @admin.action(description="Archive selected projects")
    def archive_projects(self, request, queryset):
        updated = 0
        for project in queryset.exclude(status=Project.Status.ARCHIVED):
            project.status = Project.Status.ARCHIVED
            project.updated_by = request.user
            project.save(allow_archived_change=True)
            updated += 1
        if updated:
            self.message_user(request, f"Archived {updated} project(s).", level=messages.SUCCESS)
        else:
            self.message_user(request, "No projects were archived.", level=messages.INFO)

    @admin.action(description="Unarchive selected projects")
    def unarchive_projects(self, request, queryset):
        if not self._can_unarchive(request):
            self.message_user(
                request,
                "You do not have permission to unarchive projects.",
                level=messages.ERROR,
            )
            return
        updated = 0
        for project in queryset.filter(status=Project.Status.ARCHIVED):
            project.status = Project.Status.ACTIVE
            project.updated_by = request.user
            project.save(allow_archived_change=True)
            updated += 1
        if updated:
            self.message_user(request, f"Unarchived {updated} project(s).", level=messages.SUCCESS)
        else:
            self.message_user(request, "No archived projects were selected.", level=messages.INFO)

    def _can_unarchive(self, request):
        return request.user.is_superuser or request.user.has_perm("projects.can_unarchive_project")


@admin.register(ProjectMember)
class ProjectMemberAdmin(admin.ModelAdmin):
    list_display = ("project", "user", "role", "added_by", "created_at")
    list_filter = ("role", "project__org", "project")
    search_fields = (
        "user__email",
        "user__first_name",
        "user__last_name",
        "project__name",
        "project__slug",
    )
    raw_id_fields = ("project", "user", "added_by")
