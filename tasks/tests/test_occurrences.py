from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

from django.test import TestCase

from config.test_utils import create_parent, create_room, create_task, utc
from tasks.occurrences import (
    STATUS_DONE,
    STATUS_MISSED,
    STATUS_OPEN,
    STATUS_UPCOMING,
    current_occurrence,
    local_day_bounds,
    occurrences_between,
    occurrences_on,
)

TASHKENT = ZoneInfo("Asia/Tashkent")  # UTC+5, no DST
ROME = ZoneInfo("Europe/Rome")  # DST


class DayBoundsTests(TestCase):
    def test_tashkent_day_starts_at_19_utc_previous_day(self):
        start, end = local_day_bounds(date(2026, 11, 17), TASHKENT)
        self.assertEqual(start, utc(2026, 11, 16, 19))
        self.assertEqual(end, utc(2026, 11, 17, 19))

    def test_rome_fall_back_day_has_25_hours(self):
        start, end = local_day_bounds(date(2026, 10, 25), ROME)
        self.assertEqual(end - start, timedelta(hours=25))

    def test_rome_spring_forward_day_has_23_hours(self):
        start, end = local_day_bounds(date(2026, 3, 29), ROME)
        self.assertEqual(end - start, timedelta(hours=23))


class OccurrenceGenerationTests(TestCase):
    def setUp(self):
        self.room = create_room(create_parent(), tz="Asia/Tashkent")

    def test_daily_one_per_day(self):
        task = create_task(self.room, repeat="daily", created_at=utc(2026, 11, 1))
        occs = occurrences_between([task], date(2026, 11, 10), date(2026, 11, 12), TASHKENT)
        self.assertEqual([o.local_date for o in occs], [date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12)])
        self.assertEqual(occs[0].period_start, utc(2026, 11, 9, 19))
        self.assertEqual(occs[0].period_end, utc(2026, 11, 10, 19))

    def test_daily_not_before_creation_day(self):
        # Created 15:00 local on Nov 10: that day's occurrence counts, Nov 9 does not.
        task = create_task(self.room, repeat="daily", created_at=utc(2026, 11, 10, 10))
        occs = occurrences_between([task], date(2026, 11, 8), date(2026, 11, 11), TASHKENT)
        self.assertEqual([o.local_date for o in occs], [date(2026, 11, 10), date(2026, 11, 11)])

    def test_hourly_all_day_without_window(self):
        task = create_task(self.room, repeat="hourly", created_at=utc(2026, 11, 1))
        occs = occurrences_on(task, date(2026, 11, 10), TASHKENT)
        self.assertEqual(len(occs), 24)
        self.assertEqual(occs[0].period_start, utc(2026, 11, 9, 19))
        self.assertEqual(occs[-1].period_end, utc(2026, 11, 10, 19))

    def test_hourly_window(self):
        task = create_task(
            self.room, repeat="hourly", window_start=time(9), window_end=time(20), created_at=utc(2026, 11, 1)
        )
        occs = occurrences_on(task, date(2026, 11, 10), TASHKENT)
        self.assertEqual(len(occs), 11)  # 09:00 .. 19:00 local
        self.assertEqual(occs[0].period_start, utc(2026, 11, 10, 4))  # 09:00 Tashkent
        self.assertEqual(occs[-1].period_start, utc(2026, 11, 10, 14))  # 19:00 Tashkent
        self.assertEqual(occs[-1].period_end, utc(2026, 11, 10, 15))  # 20:00 Tashkent

    def test_hourly_starts_from_creation_hour(self):
        # Created 15:30 local: the 15:00 hour counts, 14:00 does not.
        task = create_task(self.room, repeat="hourly", created_at=utc(2026, 11, 10, 10, 30))
        occs = occurrences_on(task, date(2026, 11, 10), TASHKENT)
        self.assertEqual(occs[0].period_start, utc(2026, 11, 10, 10))
        self.assertEqual(len(occs), 9)  # 15..23

    def test_once_only_on_due_date(self):
        task = create_task(self.room, repeat="once", due_date=date(2026, 11, 11), created_at=utc(2026, 11, 1))
        occs = occurrences_between([task], date(2026, 11, 10), date(2026, 11, 12), TASHKENT)
        self.assertEqual(len(occs), 1)
        self.assertEqual(occs[0].local_date, date(2026, 11, 11))

    def test_sorted_by_period_then_due_time(self):
        late = create_task(self.room, title="Late", repeat="daily", due_time=time(20), created_at=utc(2026, 11, 1))
        early = create_task(self.room, title="Early", repeat="daily", due_time=time(8), created_at=utc(2026, 11, 1))
        occs = occurrences_between([late, early], date(2026, 11, 10), date(2026, 11, 10), TASHKENT)
        self.assertEqual([o.task.title for o in occs], ["Early", "Late"])


class DstTests(TestCase):
    def setUp(self):
        self.room = create_room(create_parent(), tz="Europe/Rome")

    def test_spring_forward_skips_missing_hour(self):
        task = create_task(self.room, repeat="hourly", created_at=utc(2026, 1, 1))
        occs = occurrences_on(task, date(2026, 3, 29), ROME)
        self.assertEqual(len(occs), 23)
        starts = [o.period_start for o in occs]
        self.assertEqual(len(set(starts)), 23)
        for o in occs:
            self.assertGreater(o.period_end, o.period_start)

    def test_fall_back_covers_whole_25h_day(self):
        task = create_task(self.room, repeat="hourly", created_at=utc(2026, 1, 1))
        occs = occurrences_on(task, date(2026, 10, 25), ROME)
        total = sum((o.period_end - o.period_start for o in occs), timedelta())
        self.assertEqual(total, timedelta(hours=25))
        for a, b in zip(occs, occs[1:]):
            self.assertEqual(a.period_end, b.period_start)


class CurrentOccurrenceTests(TestCase):
    def setUp(self):
        self.room = create_room(create_parent(), tz="Asia/Tashkent")

    def test_daily_boundary(self):
        task = create_task(self.room, repeat="daily", created_at=utc(2026, 11, 1))
        before = current_occurrence(task, utc(2026, 11, 16, 18, 59, 59), TASHKENT)
        after = current_occurrence(task, utc(2026, 11, 16, 19), TASHKENT)
        self.assertEqual(before.local_date, date(2026, 11, 16))
        self.assertEqual(after.local_date, date(2026, 11, 17))
        self.assertEqual(before.period_end, after.period_start)

    def test_hourly_window_edges(self):
        task = create_task(
            self.room, repeat="hourly", window_start=time(9), window_end=time(20), created_at=utc(2026, 11, 1)
        )
        self.assertIsNone(current_occurrence(task, utc(2026, 11, 10, 3, 59), TASHKENT))  # 08:59
        at_nine = current_occurrence(task, utc(2026, 11, 10, 4), TASHKENT)  # 09:00
        self.assertEqual(at_nine.period_start, utc(2026, 11, 10, 4))
        self.assertIsNotNone(current_occurrence(task, utc(2026, 11, 10, 14, 59), TASHKENT))  # 19:59
        self.assertIsNone(current_occurrence(task, utc(2026, 11, 10, 15), TASHKENT))  # 20:00

    def test_once_only_on_due_date(self):
        task = create_task(self.room, repeat="once", due_date=date(2026, 11, 17), created_at=utc(2026, 11, 1))
        self.assertIsNone(current_occurrence(task, utc(2026, 11, 16, 18, 59), TASHKENT))
        self.assertIsNotNone(current_occurrence(task, utc(2026, 11, 16, 19), TASHKENT))
        self.assertIsNone(current_occurrence(task, utc(2026, 11, 17, 19), TASHKENT))

    def test_none_before_creation(self):
        task = create_task(self.room, repeat="hourly", created_at=utc(2026, 11, 10, 10, 30))
        self.assertIsNone(current_occurrence(task, utc(2026, 11, 10, 9, 30), TASHKENT))
        self.assertIsNotNone(current_occurrence(task, utc(2026, 11, 10, 10, 0), TASHKENT))

    def test_status(self):
        task = create_task(self.room, repeat="daily", created_at=utc(2026, 11, 1))
        occ = current_occurrence(task, utc(2026, 11, 16, 12), TASHKENT)
        self.assertEqual(occ.status(utc(2026, 11, 16, 12), False), STATUS_OPEN)
        self.assertEqual(occ.status(utc(2026, 11, 16, 12), True), STATUS_DONE)
        self.assertEqual(occ.status(utc(2026, 11, 17, 12), False), STATUS_MISSED)
        self.assertEqual(occ.status(utc(2026, 11, 15, 12), False), STATUS_UPCOMING)
