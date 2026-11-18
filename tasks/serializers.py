from rest_framework import serializers

from .days import local_today
from .models import Completion, Repeat, Task


class TaskSerializer(serializers.ModelSerializer):
    """A task with the status of the day that matters (today, or its due date)."""

    status = serializers.CharField(read_only=True)
    done_at = serializers.DateTimeField(read_only=True, allow_null=True)

    class Meta:
        model = Task
        fields = ["id", "title", "description", "repeat", "due_date", "status", "done_at", "created_at"]
        read_only_fields = fields


class TaskWriteSerializer(serializers.ModelSerializer):
    title = serializers.CharField(max_length=80)
    description = serializers.CharField(max_length=500, required=False, allow_blank=True)

    class Meta:
        model = Task
        fields = ["title", "description", "repeat", "due_date"]

    def validate(self, attrs):
        # On an edit, judge the combination of what is sent and what is stored.
        repeat = attrs.get("repeat", self.instance.repeat if self.instance else None)
        due_date = attrs["due_date"] if "due_date" in attrs else (self.instance.due_date if self.instance else None)
        if repeat == Repeat.DAILY and due_date is not None and "due_date" in attrs:
            raise serializers.ValidationError({"due_date": "A daily task has no due date."})
        return attrs

    def create(self, validated_data):
        parent = self.context["parent"]
        if validated_data["repeat"] == Repeat.ONCE and validated_data.get("due_date") is None:
            validated_data["due_date"] = local_today(parent.tzinfo)
        return Task.objects.create(parent=parent, **validated_data)

    def update(self, instance, validated_data):
        """Parent edits title, repeat or due date. Switching kind fixes due_date up."""
        repeat = validated_data.get("repeat", instance.repeat)
        if repeat == Repeat.DAILY:
            validated_data["due_date"] = None
        elif validated_data.get("due_date", instance.due_date) is None:
            validated_data["due_date"] = local_today(instance.parent.tzinfo)
        return super().update(instance, validated_data)


class TodaySerializer(serializers.Serializer):
    date = serializers.DateField()
    tasks = TaskSerializer(many=True)


class CompletionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Completion
        fields = ["id", "task", "task_title", "local_date", "done_at"]
        read_only_fields = fields


class HistoryTaskSerializer(serializers.Serializer):
    title = serializers.CharField()
    status = serializers.ChoiceField(choices=["done", "open", "missed", "upcoming"])


class HistoryDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    total = serializers.IntegerField()
    done = serializers.IntegerField()
    tasks = HistoryTaskSerializer(many=True)


class HistorySerializer(serializers.Serializer):
    from_date = serializers.DateField()
    to_date = serializers.DateField()
    days = HistoryDaySerializer(many=True)
