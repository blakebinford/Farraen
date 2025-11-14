from django.urls import path

from .quilt import QuiltQueryView, QuiltSourcesView

app_name = "welds-api"

urlpatterns = [
    path("query/", QuiltQueryView.as_view(), name="quilt-query"),
    path("sources/", QuiltSourcesView.as_view(), name="quilt-sources"),
]
