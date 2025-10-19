from django.contrib import admin
from .models import Organization, Membership, Invitation

@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "owner", "created_at")
    search_fields = ("name", "slug", "owner__email")

@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("org", "user", "role", "created_at")
    list_filter = ("role", "org")
    search_fields = ("user__email", "org__name")

@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("org", "email", "role", "invited_by", "accepted_at", "created_at")
    search_fields = ("email", "org__name")
