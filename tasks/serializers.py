from rest_framework import serializers

from .models import Completion, Repeat, Task
from .occurrences import local_today


class TaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = [
            "id",
            "room",
            "title",
            "description",
            "repeat",
            "due_date",
            "due_time",
            "window_start",
            "window_end",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "room", "created_at", "updated_at"]


class TaskWriteSerializer(serializers.ModelSerializer):
    """
    Create and edit. Fields that do not apply to the chosen repeat are
    rejected rather than silently dropped, so a client bug shows up early.
    """

    title = serializers.CharField(max_length=80)
    description = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")

    class Meta:
        model = Task
        fields = ["title", "description", "repeat", "due_date", "due_time", "window_start", "window_end", "is_active"]

    def validate(self, attrs):
        merged = {}
        if self.instance is not None:
            for name in self.Meta.fields:
                merged[name] = getattr(self.instance, name)
        merged.update(attrs)

        repeat = merged.get("repeat")
        errors = {}

        if repeat == Repeat.ONCE:
            for name in ("due_time", "window_start", "window_end"):
                if merged.get(name) is not None:
                    errors[name] = "Not used for a one-time task."
        elif repeat == Repeat.DAILY:
            for name in ("due_date", "window_start", "window_end"):
                if merged.get(name) is not None:
                    errors[name] = "Not used for a daily task."
        elif repeat == Repeat.HOURLY:
            for name in ("due_date", "due_time"):
                if merged.get(name) is not None:
                    errors[name] = "Not used for an hourly task."
            ws, we = merged.get("window_start"), merged.get("window_end")
            if (ws is None) != (we is None):
                errors["window_end"] = "Give both window_start and window_end, or neither."
            elif ws is not None and ws >= we:
                errors["window_end"] = "window_end must be after window_start."

        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def create(self, validated_data):
        room = self.context["room"]
        if validated_data["repeat"] == Repeat.ONCE and validated_data.get("due_date") is None:
            validated_data["due_date"] = local_today(room.tzinfo)
        return Task.objects.create(room=room, **validated_data)

    def update(self, instance, validated_data):
        if validated_data.get("repeat", instance.repeat) == Repeat.ONCE:
            if validated_data.get("due_date", instance.due_date) is None:
                validated_data["due_date"] = local_today(instance.room.tzinfo)
        return super().update(instance, validated_data)


class OccurrenceSerializer(serializers.Serializer):
    task_id = serializers.UUIDField()
    title = serializers.CharField()
    description = serializers.CharField()
    repeat = serializers.CharField()
    due_time = serializers.TimeField(allow_null=True)
    period_start = serializers.DateTimeField()
    period_end = serializers.DateTimeField()
    local_date = serializers.DateField()
    status = serializers.CharField()
    done_at = serializers.DateTimeField(allow_null=True)


class CompletionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Completion
        fields = ["id", "task", "task_title", "period_start", "local_date", "done_at"]
        read_only_fields = fields


class CompletedTaskSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    task_id = serializers.UUIDField(allow_null=True)
    title = serializers.CharField()
    period_start = serializers.DateTimeField()
    done_at = serializers.DateTimeField()


class CompletedDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    count = serializers.IntegerField()
    tasks = CompletedTaskSerializer(many=True)


class CompletedListSerializer(serializers.Serializer):
    from_date = serializers.DateField()
    to_date = serializers.DateField()
    days = CompletedDaySerializer(many=True)


class HistorySerializer(serializers.Serializer):
    from_date = serializers.DateField()
    to_date = serializers.DateField()
    occurrences = OccurrenceSerializer(many=True)


class DoneResponseSerializer(serializers.Serializer):
    completion = CompletionSerializer()
    occurrence = OccurrenceSerializer()
