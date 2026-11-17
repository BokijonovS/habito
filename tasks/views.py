from datetime import date, timedelta

from django.db import IntegrityError, transaction
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsChild, IsParent, IsParentOrChild
from config.exceptions import ApiError, Conflict
from notifications.service import notify_task_done_after_commit
from rooms.access import get_room_for
from rooms.models import Room

from .models import Completion, Task
from .occurrences import (
    Occurrence,
    current_occurrence,
    local_today,
    now,
    occurrences_between,
)
from .serializers import (
    CompletedListSerializer,
    CompletionSerializer,
    DoneResponseSerializer,
    HistorySerializer,
    OccurrenceSerializer,
    TaskSerializer,
    TaskWriteSerializer,
)

MAX_RANGE_DAYS = 31
DEFAULT_RANGE_DAYS = 7


# --- helpers ----------------------------------------------------------------


def _task_for(user, task_id) -> Task:
    """The task if it is in a room the caller belongs to, else 404."""
    if getattr(user, "is_parent", False):
        task = Task.objects.select_related("room").filter(pk=task_id, room__parent=user).first()
    elif getattr(user, "is_child", False):
        task = Task.objects.select_related("room").filter(pk=task_id, room=user.room).first()
    else:
        task = None
    if task is None:
        raise ApiError("not_found", "Task not found.", status.HTTP_404_NOT_FOUND)
    return task


def _parse_range(request, tz) -> tuple[date, date]:
    """?from=&to= as local dates; defaults to the last 7 days ending today."""
    today = local_today(tz)
    try:
        to_day = date.fromisoformat(request.query_params["to"]) if "to" in request.query_params else today
        from_day = (
            date.fromisoformat(request.query_params["from"])
            if "from" in request.query_params
            else to_day - timedelta(days=DEFAULT_RANGE_DAYS - 1)
        )
    except ValueError:
        raise ApiError("invalid_input", "from and to must be dates like 2026-11-17.")
    if from_day > to_day:
        raise ApiError("invalid_input", "from must not be after to.")
    if (to_day - from_day).days + 1 > MAX_RANGE_DAYS:
        raise ApiError("invalid_input", f"The range may cover at most {MAX_RANGE_DAYS} days.")
    return from_day, to_day


def _completions_by_key(room: Room, start, end) -> dict:
    rows = Completion.objects.filter(room=room, period_start__gte=start, period_start__lt=end, task__isnull=False)
    return {(c.task_id, c.period_start): c for c in rows}


def _occurrence_payload(occ: Occurrence, at, completion: Completion | None) -> dict:
    task = occ.task
    return {
        "task_id": task.id,
        "title": task.title,
        "description": task.description,
        "repeat": task.repeat,
        "due_time": task.due_time,
        "period_start": occ.period_start,
        "period_end": occ.period_end,
        "local_date": occ.local_date,
        "status": occ.status(at, completion is not None),
        "done_at": completion.done_at if completion else None,
    }


def _occurrences_payload(room: Room, from_day: date, to_day: date) -> list[dict]:
    at = now()
    tz = room.tzinfo
    tasks = list(room.tasks.filter(is_active=True))
    occs = occurrences_between(tasks, from_day, to_day, tz)
    if not occs:
        return []
    done = _completions_by_key(room, occs[0].period_start, occs[-1].period_end)
    return [_occurrence_payload(o, at, done.get((o.task.id, o.period_start))) for o in occs]


# --- tasks ------------------------------------------------------------------


class RoomTaskListView(APIView):
    permission_classes = [IsParentOrChild]

    @extend_schema(responses={200: TaskSerializer(many=True)}, tags=["tasks"])
    def get(self, request, room_id):
        room = get_room_for(request.user, room_id)
        return Response(TaskSerializer(room.tasks.all(), many=True).data)

    @extend_schema(request=TaskWriteSerializer, responses={201: TaskSerializer}, tags=["tasks"])
    def post(self, request, room_id):
        if not IsParent().has_permission(request, self):
            raise ApiError("forbidden", "Only the parent can create tasks.", status.HTTP_403_FORBIDDEN)
        room = get_room_for(request.user, room_id)
        serializer = TaskWriteSerializer(data=request.data, context={"room": room})
        serializer.is_valid(raise_exception=True)
        task = serializer.save()
        return Response(TaskSerializer(task).data, status=status.HTTP_201_CREATED)


class TaskDetailView(APIView):
    permission_classes = [IsParent]

    @extend_schema(request=TaskWriteSerializer, responses={200: TaskSerializer}, tags=["tasks"])
    def patch(self, request, task_id):
        task = _task_for(request.user, task_id)
        serializer = TaskWriteSerializer(task, data=request.data, partial=True, context={"room": task.room})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(TaskSerializer(task).data)

    @extend_schema(responses={204: None}, tags=["tasks"])
    def delete(self, request, task_id):
        task = _task_for(request.user, task_id)
        task.delete()  # completions survive with task=null
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- today, completed, history ---------------------------------------------


class TodayView(APIView):
    permission_classes = [IsParentOrChild]

    @extend_schema(responses={200: OccurrenceSerializer(many=True)}, tags=["tasks"])
    def get(self, request, room_id):
        room = get_room_for(request.user, room_id)
        today = local_today(room.tzinfo)
        return Response({"date": today, "occurrences": _occurrences_payload(room, today, today)})


_RANGE_PARAMS = [
    OpenApiParameter("from", str, description="First local day, YYYY-MM-DD. Default: 6 days before `to`."),
    OpenApiParameter("to", str, description="Last local day, YYYY-MM-DD. Default: today in the room's timezone."),
]


class CompletedView(APIView):
    permission_classes = [IsParentOrChild]

    @extend_schema(parameters=_RANGE_PARAMS, responses={200: CompletedListSerializer}, tags=["tasks"])
    def get(self, request, room_id):
        room = get_room_for(request.user, room_id)
        from_day, to_day = _parse_range(request, room.tzinfo)
        rows = Completion.objects.filter(room=room, local_date__gte=from_day, local_date__lte=to_day).order_by(
            "-local_date", "-done_at"
        )
        days: list[dict] = []
        for c in rows:
            if not days or days[-1]["date"] != c.local_date:
                days.append({"date": c.local_date, "count": 0, "tasks": []})
            days[-1]["count"] += 1
            days[-1]["tasks"].append(
                {"id": c.id, "task_id": c.task_id, "title": c.task_title, "period_start": c.period_start, "done_at": c.done_at}
            )
        return Response(CompletedListSerializer({"from_date": from_day, "to_date": to_day, "days": days}).data)


class HistoryView(APIView):
    permission_classes = [IsParentOrChild]

    @extend_schema(parameters=_RANGE_PARAMS, responses={200: HistorySerializer}, tags=["tasks"])
    def get(self, request, room_id):
        room = get_room_for(request.user, room_id)
        from_day, to_day = _parse_range(request, room.tzinfo)
        occurrences = _occurrences_payload(room, from_day, to_day)
        return Response(HistorySerializer({"from_date": from_day, "to_date": to_day, "occurrences": occurrences}).data)


# --- done -------------------------------------------------------------------


class TaskDoneView(APIView):
    """A done mark is final: there is no undo in v1."""

    permission_classes = [IsChild]

    @extend_schema(request=None, responses={201: DoneResponseSerializer}, tags=["tasks"])
    def post(self, request, task_id):
        task = _task_for(request.user, task_id)
        if not task.is_active:
            raise Conflict("task_paused", "This task is paused.")
        at = now()
        occ = current_occurrence(task, at, task.room.tzinfo)
        if occ is None:
            raise Conflict("no_open_occurrence", "This task is not open right now.")

        try:
            with transaction.atomic():
                completion = Completion.objects.create(
                    task=task,
                    room=task.room,
                    task_title=task.title,
                    period_start=occ.period_start,
                    local_date=occ.local_date,
                    done_at=at,
                )
                notify_task_done_after_commit(completion, request.user.display_name)
        except IntegrityError:
            raise Conflict("already_done", "This task is already marked done.")

        return Response(
            {
                "completion": CompletionSerializer(completion).data,
                "occurrence": OccurrenceSerializer(_occurrence_payload(occ, at, completion)).data,
            },
            status=status.HTTP_201_CREATED,
        )
