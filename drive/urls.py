# drive/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path("drive/", views.project_drive_root, name="project_drive_root"),
    path("drive/f/<int:folder_id>/", views.folder_view, name="drive_folder"),
    path("drive/f/<int:folder_id>/new-folder/", views.folder_create, name="drive_folder_create"),
    path("drive/f/<int:folder_id>/upload/", views.file_upload, name="drive_upload"),

    path("drive/file/<int:file_id>/", views.file_detail, name="drive_file"),
    path(
        "drive/file/<int:file_id>/drawer/",
        views.file_detail_drawer,
        name="drive_file_drawer",
    ),
    path(
        "drive/file/<int:file_id>/inline-update/",
        views.file_inline_update,
        name="drive_file_inline_update",
    ),
    path("drive/file/<int:file_id>/download/", views.file_download_latest, name="drive_file_download"),
    path("drive/file/<int:file_id>/preview/", views.file_stream_latest, name="drive_file_stream"),  # NEW

    path("drive/file/<int:file_id>/versions/<int:version>/download/", views.file_download_version, name="drive_file_download_version"),
    path("drive/file/<int:file_id>/versions/<int:version>/preview/", views.file_stream_version, name="drive_file_stream_version"),
    path(
        "drive/file/<int:file_id>/versions/<int:version>/revert/",
        views.file_revert_version,
        name="drive_file_revert_version",
    ),
    path("drive/file/<int:file_id>/checkout/", views.file_checkout, name="drive_file_checkout"),
    path("drive/file/<int:file_id>/checkin/", views.file_checkin, name="drive_file_checkin"),
    path("drive/file/<int:file_id>/force-checkin/", views.file_force_checkin, name="drive_file_force_checkin"),
    path(
        "drive/file/<int:file_id>/approve-mtr/",
        views.file_manual_approve_mtr,
        name="drive_mtr_manual_approve",
    ),
]
