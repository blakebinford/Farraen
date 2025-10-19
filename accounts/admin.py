from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User

@admin.register(User)
class MyUserAdmin(UserAdmin):
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("first_name", "last_name", "kind", "primary_org")}),
        ("Permissions", {"fields": ("is_active","is_staff","is_superuser","groups","user_permissions")}),
        ("Important dates", {"fields": ("last_login","date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email","password1","password2","kind")}),
    )
    list_display = ("email", "kind", "primary_org", "is_staff", "last_login")
    list_filter = ("kind", "is_staff", "is_superuser", "is_active")
    search_fields = ("email",)
    ordering = ("email",)

