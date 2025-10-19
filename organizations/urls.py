from django.urls import path
from . import views

urlpatterns = [
    path("", views.org_dashboard, name="org_dashboard"),
    path("members/", views.org_members, name="org_members"),
    path("invite/", views.invite_member, name="invite_member"),
    path("invite/accept/<str:token>/", views.accept_invite, name="accept_invite"),
]
