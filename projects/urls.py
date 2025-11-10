from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    path("projects/", views.project_list, name="project_list"),
    path("projects/new/", views.project_create, name="project_create"),
    path(
        "projects/<slug:project_slug>/weld-dashboard/",
        views.ProjectWeldDashboardView.as_view(),
        name="weld_dashboard",
    ),
]
