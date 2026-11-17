import uuid

from django.db import models

from rooms.models import Room


class Repeat(models.TextChoices):
    ONCE = "once", "Once"
    DAILY = "daily", "Daily"
    HOURLY = "hourly", "Hourly"


class Task(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=80)
    description = models.CharField(max_length=500, blank=True, default="")
    repeat = models.CharField(max_length=10, choices=Repeat.choices)
    # once: the day it is due (defaults to the day it was created, room time)
    due_date = models.DateField(null=True, blank=True)
    # daily: the target time within the day, informational
    due_time = models.TimeField(null=True, blank=True)
    # hourly: occurrences from window_start up to (not including) window_end
    window_start = models.TimeField(null=True, blank=True)
    window_end = models.TimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return self.title


class Completion(models.Model):
    """
    One row per task occurrence the child marked done. Undo deletes the row.
    Survives the task being renamed or deleted, so the completed list stays
    readable.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.SET_NULL, null=True, blank=True, related_name="completions")
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="completions")
    task_title = models.CharField(max_length=80)
    period_start = models.DateTimeField()
    local_date = models.DateField()
    done_at = models.DateTimeField()

    class Meta:
        ordering = ["-done_at"]
        constraints = [
            models.UniqueConstraint(fields=["task", "period_start"], name="completion_task_period_unique"),
        ]
        indexes = [
            models.Index(fields=["room", "local_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.task_title} @ {self.period_start:%Y-%m-%d %H:%M}"
