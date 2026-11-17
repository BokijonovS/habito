from django.contrib import admin

from .models import Child, Room


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ["name", "parent", "timezone", "join_code", "join_code_expires_at", "created_at"]
    search_fields = ["name", "parent__email"]


@admin.register(Child)
class ChildAdmin(admin.ModelAdmin):
    list_display = ["display_name", "room", "joined_at"]
