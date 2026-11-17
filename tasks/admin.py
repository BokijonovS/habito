from django.contrib import admin

from .models import Completion, Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ["title", "room", "repeat", "is_active", "created_at"]
    list_filter = ["repeat", "is_active"]


@admin.register(Completion)
class CompletionAdmin(admin.ModelAdmin):
    list_display = ["task_title", "room", "local_date", "period_start", "done_at"]
