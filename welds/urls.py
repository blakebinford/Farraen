from django.urls import path

from . import views


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
]
