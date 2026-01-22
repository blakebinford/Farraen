from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    path("projects/", views.project_list, name="project_list"),
    path("projects/new/", views.project_create, name="project_create"),
    path(
        "projects/<slug:project_slug>/dashboard/",
        views.ProjectDashboardView.as_view(),
        name="project_dashboard",
    ),
    path(
        "projects/<slug:project_slug>/weld-dashboard/",
        views.ProjectWeldDashboardView.as_view(),
        name="weld_dashboard",
    ),
    path(
        "projects/<slug:project_slug>/members/",
        views.project_members,
        name="project_members",
    ),
]
