from django.contrib import admin

from .models import MaterialHeat, NDERig, Welder, Weld, WeldHistory


@admin.register(MaterialHeat)
class MaterialHeatAdmin(admin.ModelAdmin):
    list_display = (
        "heat_number",
        "org",
        "material_grade",
        "outer_diameter_in",
        "wall_thickness_in",
        "is_active",
    )
    list_filter = ("org", "is_active")
    search_fields = ("heat_number", "description", "material_grade")
    ordering = ("org", "heat_number")


@admin.register(Welder)
class WelderAdmin(admin.ModelAdmin):
    list_display = ("name", "stencil", "org", "is_active")
    list_filter = ("org", "is_active")
    search_fields = ("name", "stencil", "employee_id")
    ordering = ("org", "stencil")
    filter_horizontal = ("approved_wps",)


@admin.register(NDERig)
class NDERigAdmin(admin.ModelAdmin):
    list_display = ("name", "org", "project", "is_active", "qualification_folder")
    list_filter = ("org", "project", "is_active")
    search_fields = ("name",)
    ordering = ("org", "name")
    readonly_fields = ("qualification_folder",)


@admin.register(Weld)
class WeldAdmin(admin.ModelAdmin):
    list_display = (
        "weld_id",
        "project",
        "weld_type_display",
        "repair_type_display",
        "disposition_display",
        "nde_type_display",
        "nde_number",
    )
    list_filter = (
        "project",
        "weld_type",
        "repair_type",
        "disposition",
        "nde_type",
        "nde_rig",
    )
    search_fields = (
        "weld_id",
        "nde_number",
        "drawing_number",
        "material1_description",
        "material2_description",
    )
    ordering = ("project", "weld_id")

    @admin.display(description="Weld type")
    def weld_type_display(self, obj):
        return obj.get_weld_type_display() or ""

    @admin.display(description="Repair type")
    def repair_type_display(self, obj):
        return obj.get_repair_type_display() or ""

    @admin.display(description="Disposition")
    def disposition_display(self, obj):
        return obj.get_disposition_display() or ""

    @admin.display(description="NDE type")
    def nde_type_display(self, obj):
        return obj.get_nde_type_display() or ""


@admin.register(WeldHistory)
class WeldHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "weld",
        "change_type",
        "changed_by",
        "created_at",
    )
    list_filter = ("change_type", "created_at")
    search_fields = ("weld__weld_id", "reason", "changed_fields")
    ordering = ("-created_at",)
