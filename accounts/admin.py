from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Parent, ParentDevice


@admin.register(Parent)
class ParentAdmin(UserAdmin):
    ordering = ["email"]
    list_display = ["email", "display_name", "is_staff", "created_at"]
    search_fields = ["email", "display_name"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("display_name",)}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "display_name", "password1", "password2")}),)


@admin.register(ParentDevice)
class ParentDeviceAdmin(admin.ModelAdmin):
    list_display = ["id", "parent", "created_at", "last_used_at"]
