from django.contrib import admin

from .models import MaterialHeat, NDERig, Weld


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


@admin.register(NDERig)
class NDERigAdmin(admin.ModelAdmin):
    list_display = ("name", "org", "is_active")
    list_filter = ("org", "is_active")
    search_fields = ("name",)
    ordering = ("org", "name")


@admin.register(Weld)
class WeldAdmin(admin.ModelAdmin):
    list_display = (
        "weld_id",
        "project",
        "weld_type",
        "disposition",
        "nde_number",
    )
    list_filter = ("project", "disposition", "nde_rig")
    search_fields = (
        "weld_id",
        "nde_number",
        "drawing_number",
        "material1_description",
        "material2_description",
    )
    ordering = ("project", "weld_id")
