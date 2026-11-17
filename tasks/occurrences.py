"""
Occurrences are computed, never stored. A daily task has one per local day,
an hourly task one per local hour inside its window, a one-time task one on
its due date. Everything here works in the room's timezone and returns UTC
instants for the period boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import Repeat, Task

UTC = dt_timezone.utc

STATUS_OPEN = "open"
STATUS_DONE = "done"
STATUS_MISSED = "missed"
STATUS_UPCOMING = "upcoming"


def now() -> datetime:
    """Single clock for this module so tests can freeze it."""
    return timezone.now()


@dataclass(frozen=True)
class Occurrence:
    task: Task
    period_start: datetime  # UTC
    period_end: datetime  # UTC, exclusive
    local_date: date

    def status(self, at: datetime, done: bool) -> str:
        if done:
            return STATUS_DONE
        if at < self.period_start:
            return STATUS_UPCOMING
        if at < self.period_end:
            return STATUS_OPEN
        return STATUS_MISSED


def local_day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return start, end


def local_hour_bounds(day: date, hour: int, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(hour), tzinfo=tz).astimezone(UTC)
    if hour == 23:
        end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    else:
        end = datetime.combine(day, time(hour + 1), tzinfo=tz).astimezone(UTC)
    return start, end


def _hour_in_window(task: Task, hour: int) -> bool:
    if task.window_start is None or task.window_end is None:
        return True
    t = time(hour)
    return task.window_start <= t < task.window_end


def _counts(task: Task, occ_end: datetime) -> bool:
    """An occurrence counts only if it ends after the task was created."""
    return occ_end > task.created_at


def occurrences_on(task: Task, day: date, tz: ZoneInfo) -> list[Occurrence]:
    """All occurrences of a task on one local day, in order."""
    result: list[Occurrence] = []
    if task.repeat == Repeat.ONCE:
        if task.due_date == day:
            start, end = local_day_bounds(day, tz)
            if _counts(task, end):
                result.append(Occurrence(task, start, end, day))
    elif task.repeat == Repeat.DAILY:
        start, end = local_day_bounds(day, tz)
        if _counts(task, end):
            result.append(Occurrence(task, start, end, day))
    elif task.repeat == Repeat.HOURLY:
        seen: set[datetime] = set()
        for hour in range(24):
            if not _hour_in_window(task, hour):
                continue
            start, end = local_hour_bounds(day, hour, tz)
            if end <= start or start in seen:  # hour skipped by a DST gap
                continue
            seen.add(start)
            if _counts(task, end):
                result.append(Occurrence(task, start, end, day))
    return result


def occurrences_between(tasks, start_day: date, end_day: date, tz: ZoneInfo) -> list[Occurrence]:
    """All occurrences of the given tasks from start_day to end_day inclusive."""
    result: list[Occurrence] = []
    day = start_day
    while day <= end_day:
        for task in tasks:
            result.extend(occurrences_on(task, day, tz))
        day += timedelta(days=1)
    result.sort(key=lambda o: (o.period_start, o.task.due_time or time.min, o.task.created_at))
    return result


def current_occurrence(task: Task, at: datetime, tz: ZoneInfo) -> Occurrence | None:
    """The occurrence whose period contains `at`, or None if there is none."""
    local = at.astimezone(tz)
    day = local.date()
    if task.repeat == Repeat.ONCE:
        if task.due_date != day:
            return None
        start, end = local_day_bounds(day, tz)
    elif task.repeat == Repeat.DAILY:
        start, end = local_day_bounds(day, tz)
    elif task.repeat == Repeat.HOURLY:
        if not _hour_in_window(task, local.hour):
            return None
        start, end = local_hour_bounds(day, local.hour, tz)
    else:  # pragma: no cover
        return None
    if not (start <= at < end) or not _counts(task, end):
        return None
    return Occurrence(task, start, end, day)


def local_today(tz: ZoneInfo, at: datetime | None = None) -> date:
    return (at or now()).astimezone(tz).date()
