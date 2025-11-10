from django.urls import path

from . import views


app_name = "welds"


urlpatterns = [
    path(
        "projects/<slug:project_slug>/weld-log/",
        views.weld_log,
        name="weld_log",
    ),
    path(
        "projects/<slug:project_slug>/weld-log/data/",
        views.weld_log_data,
        name="weld_log_data",
    ),
    path(
        "projects/<slug:project_slug>/weld-log/options/heats/",
        views.material_heat_options,
        name="weld_material_heat_options",
    ),
    path(
        "projects/<slug:project_slug>/weld-log/options/heats/search/",
        views.material_heat_search,
        name="weld_material_heat_search",
    ),
    path(
        "projects/<slug:project_slug>/weld-log/options/welders/",
        views.welder_options,
        name="weld_welder_options",
    ),
    path(
        "projects/<slug:project_slug>/weld-log/options/nde-rigs/",
        views.nde_rig_options,
        name="weld_nde_rig_options",
    ),
]
