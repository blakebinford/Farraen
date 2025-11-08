from django.contrib import admin

from .models import Weld


@admin.register(Weld)
class WeldAdmin(admin.ModelAdmin):
    list_display = ("weld_id", "project", "status", "welder", "wps", "heat_number")
    list_filter = ("project", "status")
    search_fields = ("weld_id", "welder", "wps", "heat_number", "location")
    ordering = ("project", "weld_id")
