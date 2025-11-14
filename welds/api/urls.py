from django.urls import path

from . import quilt

app_name = "welds-api"

urlpatterns = [
    path("query/", quilt.quilt_query, name="quilt-query"),
    path("sources/", quilt.quilt_sources, name="quilt-sources"),
]
