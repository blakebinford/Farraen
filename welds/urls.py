from django.urls import path

from django.urls import path

from . import views


app_name = "welds"


urlpatterns = [
    path("mtrs/drafts/", views.list_mtr_drafts, name="mtr_draft_list"),
    path(
        "mtrs/drafts/<int:draft_id>/",
        views.verify_mtr_draft,
        name="verify_mtr_draft",
    ),
    path(
        "projects/<slug:project_slug>/weld-log/",
        views.weld_log,
        name="weld_log",
    ),
    path(
        "projects/<slug:project_slug>/repair-log/",
        views.repair_log,
        name="repair_log",
    ),
    path(
        "projects/<slug:project_slug>/weld-log/data/",
        views.weld_log_data,
        name="weld_log_data",
    ),
    path(
        "projects/<slug:project_slug>/weld-log/history/",
        views.weld_history_data,
        name="weld_history_data",
    ),
    path(
        "projects/<slug:project_slug>/weld-log/history/view/",
        views.weld_history_page,
        name="weld_history_page",
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
    path(
        "projects/<slug:project_slug>/weld-dashboard/analytics/",
        views.weld_dashboard_analytics,
        name="weld_dashboard_analytics",
    ),
    path(
        "projects/<slug:project_slug>/weld-dashboard/drilldown/",
        views.weld_dashboard_drilldown,
        name="weld_dashboard_drilldown",
    ),
    path(
        "projects/<slug:project_slug>/weld-log/welds/<int:weld_pk>/history/",
        views.weld_history,
        name="weld_history",
    ),
    path(
        "projects/<slug:project_slug>/weld-log/welds/history/<int:history_id>/rollback/",
        views.weld_history_rollback,
        name="weld_history_rollback",
    ),
    path(
        "welds/<int:weld_id>/mark_repair/",
        views.mark_weld_for_repair,
        name="mark_weld_for_repair",
    ),
    path(
        "projects/<int:project_id>/repairs/",
        views.project_repairs,
        name="project_repairs",
    ),
    path(
        "repairs/<int:repair_id>/",
        views.update_repair,
        name="update_repair",
    ),
    path(
        "repairs/<int:repair_id>/attempts/",
        views.create_repair_attempt,
        name="repair_add_attempt",
    ),
    path(
        "repairs/<int:repair_id>/reinspections/",
        views.create_repair_reinspection,
        name="repair_add_reinspection",
    ),
    path(
        "repairs/<int:repair_id>/close/",
        views.close_repair,
        name="repair_close",
    ),
]
