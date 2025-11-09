from django.urls import path
from . import views

urlpatterns = [
    path("docs/", views.doc_list, name="doc_list"),
    path("docs/upload/", views.doc_upload, name="doc_upload"),
    path(
        "docs/<int:doc_id>/inline-update/",
        views.doc_inline_update,
        name="doc_inline_update",
    ),
    path("docs/<int:doc_id>/", views.doc_detail, name="doc_detail"),
]
