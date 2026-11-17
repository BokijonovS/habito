from datetime import date, time

from django.urls import reverse

from accounts.models import ParentDevice
from config.test_utils import (
    HabitoTestCase,
    child_client,
    create_parent,
    create_room,
    create_task,
    frozen_now,
    join_room,
    parent_client,
    utc,
)
from tasks.models import Completion

# Asia/Tashkent is UTC+5 all year: local midnight is 19:00 UTC the day before.


class DoneTests(HabitoTestCase):
    def setUp(self):
        super().setUp()
        self.parent = create_parent()
        self.room = create_room(self.parent, tz="Asia/Tashkent")
        self.token = join_room(self.room, "Ali")
        self.child = child_client(self.token)
        self.daily = create_task(self.room, "Brush teeth", "daily", created_at=utc(2026, 11, 1))
        self.hourly = create_task(
            self.room, "Drink water", "hourly", window_start=time(9), window_end=time(20), created_at=utc(2026, 11, 1)
        )
        self.once = create_task(self.room, "Dentist", "once", due_date=date(2026, 11, 17), created_at=utc(2026, 11, 1))

    def done(self, task, at):
        with frozen_now(at), self.captureOnCommitCallbacks(execute=True):
            return self.child.post(reverse("task-done", args=[task.id]))

    def undo(self, task, at):
        with frozen_now(at):
            return self.child.delete(reverse("task-done", args=[task.id]))

    def test_daily_done_just_before_local_midnight(self):
        response = self.done(self.daily, utc(2026, 11, 16, 18, 59))  # 23:59 Nov 16 local
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["completion"]["local_date"], "2026-11-16")
        self.assertEqual(body["completion"]["period_start"], "2026-11-15T19:00:00Z")
        self.assertEqual(body["occurrence"]["status"], "done")
        self.assertEqual(Completion.objects.get().task_title, "Brush teeth")

    def test_daily_new_occurrence_after_local_midnight(self):
        self.done(self.daily, utc(2026, 11, 16, 18, 59))
        response = self.done(self.daily, utc(2026, 11, 16, 19, 0))  # 00:00 Nov 17 local
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["completion"]["local_date"], "2026-11-17")
        self.assertEqual(Completion.objects.count(), 2)

    def test_already_done(self):
        self.done(self.daily, utc(2026, 11, 16, 10))
        response = self.done(self.daily, utc(2026, 11, 16, 11))
        self.assertError(response, 409, "already_done")
        self.assertEqual(Completion.objects.count(), 1)

    def test_hourly_outside_window(self):
        self.assertError(self.done(self.hourly, utc(2026, 11, 16, 3, 59)), 409, "no_open_occurrence")  # 08:59
        self.assertError(self.done(self.hourly, utc(2026, 11, 16, 15, 0)), 409, "no_open_occurrence")  # 20:00

    def test_hourly_each_hour_is_separate(self):
        first = self.done(self.hourly, utc(2026, 11, 16, 4, 5))  # 09:05
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(first.json()["completion"]["period_start"], "2026-11-16T04:00:00Z")
        self.assertError(self.done(self.hourly, utc(2026, 11, 16, 4, 50)), 409, "already_done")
        second = self.done(self.hourly, utc(2026, 11, 16, 5, 0))  # 10:00
        self.assertEqual(second.status_code, 201)
        self.assertEqual(Completion.objects.count(), 2)

    def test_once_only_on_due_date(self):
        self.assertError(self.done(self.once, utc(2026, 11, 16, 18, 59)), 409, "no_open_occurrence")
        self.assertEqual(self.done(self.once, utc(2026, 11, 16, 19)).status_code, 201)
        self.assertError(self.done(self.once, utc(2026, 11, 17, 19)), 409, "no_open_occurrence")

    def test_paused_task(self):
        self.daily.is_active = False
        self.daily.save()
        self.assertError(self.done(self.daily, utc(2026, 11, 16, 10)), 409, "task_paused")

    def test_undo_within_period(self):
        self.done(self.daily, utc(2026, 11, 16, 10))
        response = self.undo(self.daily, utc(2026, 11, 16, 12))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(Completion.objects.count(), 0)
        # Can be marked again.
        self.assertEqual(self.done(self.daily, utc(2026, 11, 16, 13)).status_code, 201)

    def test_undo_after_period_ended(self):
        self.done(self.daily, utc(2026, 11, 16, 18, 59))
        response = self.undo(self.daily, utc(2026, 11, 16, 19, 0))
        self.assertError(response, 409, "period_ended")
        self.assertEqual(Completion.objects.count(), 1)

    def test_undo_hourly_after_hour_ended(self):
        self.done(self.hourly, utc(2026, 11, 16, 4, 5))
        self.assertEqual(self.undo(self.hourly, utc(2026, 11, 16, 4, 30)).status_code, 204)
        self.done(self.hourly, utc(2026, 11, 16, 4, 40))
        self.assertError(self.undo(self.hourly, utc(2026, 11, 16, 5, 0)), 409, "period_ended")

    def test_undo_when_not_done(self):
        self.assertError(self.undo(self.daily, utc(2026, 11, 16, 10)), 409, "not_done")

    def test_parent_cannot_mark_done(self):
        with frozen_now(utc(2026, 11, 16, 10)):
            response = parent_client(self.parent).post(reverse("task-done", args=[self.daily.id]))
        self.assertError(response, 403, "forbidden")
        self.assertEqual(Completion.objects.count(), 0)

    def test_child_cannot_mark_other_rooms_task(self):
        other_task = create_task(create_room(create_parent("dad@example.com")), "Theirs", "daily")
        self.assertError(self.done(other_task, utc(2026, 11, 16, 10)), 404, "not_found")

    def test_unknown_task(self):
        with frozen_now(utc(2026, 11, 16, 10)):
            response = self.child.post(reverse("task-done", args=["00000000-0000-4000-8000-000000000000"]))
        self.assertError(response, 404, "not_found")

    def test_push_sent_to_every_parent_phone(self):
        ParentDevice.objects.create(parent=self.parent, apns_token="phone-1")
        ParentDevice.objects.create(parent=self.parent, apns_token="phone-2")
        ParentDevice.objects.create(parent=create_parent("dad@example.com"), apns_token="someone-else")
        self.done(self.daily, utc(2026, 11, 16, 10))
        self.assertEqual(len(self.sent_pushes), 1)
        push = self.sent_pushes[0]
        self.assertEqual(sorted(push.tokens), ["phone-1", "phone-2"])
        self.assertEqual(push.title, "Ali finished a task")
        self.assertEqual(push.body, "Brush teeth")
        self.assertEqual(push.data["type"], "task_done")
        self.assertIsNotNone(ParentDevice.objects.get(apns_token="phone-1").last_used_at)

    def test_no_push_without_devices(self):
        self.done(self.daily, utc(2026, 11, 16, 10))
        self.assertEqual(self.sent_pushes, [])

    def test_no_push_on_failed_done(self):
        ParentDevice.objects.create(parent=self.parent, apns_token="phone-1")
        self.done(self.daily, utc(2026, 11, 16, 10))
        self.done(self.daily, utc(2026, 11, 16, 11))  # already done
        self.assertEqual(len(self.sent_pushes), 1)


class TodayTests(HabitoTestCase):
    def setUp(self):
        super().setUp()
        self.parent = create_parent()
        self.room = create_room(self.parent, tz="Asia/Tashkent")
        self.token = join_room(self.room)
        self.daily = create_task(self.room, "Brush teeth", "daily", due_time=time(8), created_at=utc(2026, 11, 1))
        self.hourly = create_task(
            self.room, "Water", "hourly", window_start=time(9), window_end=time(12), created_at=utc(2026, 11, 1)
        )
        create_task(self.room, "Paused", "daily", is_active=False, created_at=utc(2026, 11, 1))
        create_task(self.room, "Dentist", "once", due_date=date(2026, 11, 20), created_at=utc(2026, 11, 1))
        self.url = reverse("room-today", args=[self.room.id])

    def test_today_for_child(self):
        with frozen_now(utc(2026, 11, 16, 5, 30)):  # 10:30 local Nov 16
            response = child_client(self.token).get(self.url)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["date"], "2026-11-16")
        titles = [(o["title"], o["status"]) for o in body["occurrences"]]
        self.assertEqual(
            titles,
            [("Brush teeth", "open"), ("Water", "missed"), ("Water", "open"), ("Water", "upcoming")],
        )

    def test_today_marks_done(self):
        Completion.objects.create(
            task=self.daily,
            room=self.room,
            task_title="Brush teeth",
            period_start=utc(2026, 11, 15, 19),
            local_date=date(2026, 11, 16),
            done_at=utc(2026, 11, 16, 3),
        )
        with frozen_now(utc(2026, 11, 16, 5, 30)):
            body = parent_client(self.parent).get(self.url).json()
        daily = next(o for o in body["occurrences"] if o["title"] == "Brush teeth")
        self.assertEqual(daily["status"], "done")
        self.assertEqual(daily["done_at"], "2026-11-16T03:00:00Z")

    def test_today_uses_room_timezone(self):
        with frozen_now(utc(2026, 11, 16, 20)):  # 01:00 Nov 17 local
            body = parent_client(self.parent).get(self.url).json()
        self.assertEqual(body["date"], "2026-11-17")

    def test_once_appears_on_its_day(self):
        with frozen_now(utc(2026, 11, 20, 5)):
            body = parent_client(self.parent).get(self.url).json()
        self.assertIn("Dentist", [o["title"] for o in body["occurrences"]])

    def test_other_room_404(self):
        other = create_room(create_parent("dad@example.com"))
        self.assertError(parent_client(self.parent).get(reverse("room-today", args=[other.id])), 404)
        self.assertError(child_client(self.token).get(reverse("room-today", args=[other.id])), 404)


class CompletedTests(HabitoTestCase):
    def setUp(self):
        super().setUp()
        self.parent = create_parent()
        self.room = create_room(self.parent, tz="Asia/Tashkent")
        self.token = join_room(self.room)
        self.task = create_task(self.room, "Brush teeth", "daily", created_at=utc(2026, 11, 1))
        bed = create_task(self.room, "Make the bed", "daily", created_at=utc(2026, 11, 1))
        self._complete(self.task, date(2026, 11, 16), utc(2026, 11, 16, 14, 5))
        self._complete(self.task, date(2026, 11, 17), utc(2026, 11, 17, 2, 42))
        self._complete(bed, date(2026, 11, 17), utc(2026, 11, 17, 2, 30))
        self._complete(self.task, date(2026, 11, 10), utc(2026, 11, 10, 2))
        self.url = reverse("room-completed", args=[self.room.id])

    def _complete(self, task, local_date, done_at):
        from tasks.occurrences import local_day_bounds

        start, _ = local_day_bounds(local_date, self.room.tzinfo)
        Completion.objects.create(
            task=task, room=self.room, task_title=task.title, period_start=start, local_date=local_date, done_at=done_at
        )

    def test_grouped_newest_first(self):
        response = parent_client(self.parent).get(self.url, {"from": "2026-11-16", "to": "2026-11-17"})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["from_date"], "2026-11-16")
        self.assertEqual(body["to_date"], "2026-11-17")
        self.assertEqual([d["date"] for d in body["days"]], ["2026-11-17", "2026-11-16"])
        self.assertEqual(body["days"][0]["count"], 2)
        self.assertEqual([t["title"] for t in body["days"][0]["tasks"]], ["Brush teeth", "Make the bed"])
        self.assertEqual(body["days"][0]["tasks"][0]["done_at"], "2026-11-17T02:42:00Z")
        self.assertEqual(body["days"][1]["count"], 1)

    def test_default_range_is_last_7_days(self):
        with frozen_now(utc(2026, 11, 17, 10)):
            body = parent_client(self.parent).get(self.url).json()
        self.assertEqual(body["from_date"], "2026-11-11")
        self.assertEqual(body["to_date"], "2026-11-17")
        self.assertEqual([d["date"] for d in body["days"]], ["2026-11-17", "2026-11-16"])

    def test_child_can_read(self):
        response = child_client(self.token).get(self.url, {"from": "2026-11-10", "to": "2026-11-10"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["days"][0]["count"], 1)

    def test_renamed_or_deleted_task_keeps_title(self):
        self.task.title = "Brush teeth twice"
        self.task.save()
        body = parent_client(self.parent).get(self.url, {"from": "2026-11-16", "to": "2026-11-16"}).json()
        self.assertEqual(body["days"][0]["tasks"][0]["title"], "Brush teeth")
        self.task.delete()
        body = parent_client(self.parent).get(self.url, {"from": "2026-11-16", "to": "2026-11-16"}).json()
        self.assertEqual(body["days"][0]["tasks"][0]["title"], "Brush teeth")
        self.assertIsNone(body["days"][0]["tasks"][0]["task_id"])

    def test_bad_ranges(self):
        client = parent_client(self.parent)
        self.assertError(client.get(self.url, {"from": "yesterday"}), 400, "invalid_input")
        self.assertError(client.get(self.url, {"from": "2026-11-17", "to": "2026-11-16"}), 400, "invalid_input")
        self.assertError(client.get(self.url, {"from": "2026-10-01", "to": "2026-11-17"}), 400, "invalid_input")
        ok = client.get(self.url, {"from": "2026-10-18", "to": "2026-11-17"})
        self.assertEqual(ok.status_code, 200)

    def test_other_room_404(self):
        other = create_room(create_parent("dad@example.com"))
        self.assertError(parent_client(self.parent).get(reverse("room-completed", args=[other.id])), 404)


class HistoryTests(HabitoTestCase):
    def setUp(self):
        super().setUp()
        self.parent = create_parent()
        self.room = create_room(self.parent, tz="Asia/Tashkent")
        self.token = join_room(self.room)
        self.daily = create_task(self.room, "Brush teeth", "daily", created_at=utc(2026, 11, 1))
        self.url = reverse("room-history", args=[self.room.id])

    def test_history_has_missed_and_done(self):
        Completion.objects.create(
            task=self.daily,
            room=self.room,
            task_title="Brush teeth",
            period_start=utc(2026, 11, 14, 19),
            local_date=date(2026, 11, 15),
            done_at=utc(2026, 11, 15, 3),
        )
        with frozen_now(utc(2026, 11, 16, 10)):
            response = child_client(self.token).get(self.url, {"from": "2026-11-14", "to": "2026-11-16"})
        self.assertEqual(response.status_code, 200, response.content)
        occs = response.json()["occurrences"]
        self.assertEqual([(o["local_date"], o["status"]) for o in occs], [
            ("2026-11-14", "missed"),
            ("2026-11-15", "done"),
            ("2026-11-16", "open"),
        ])
        self.assertEqual(occs[1]["done_at"], "2026-11-15T03:00:00Z")

    def test_history_starts_at_task_creation(self):
        with frozen_now(utc(2026, 11, 16, 10)):
            body = parent_client(self.parent).get(self.url, {"from": "2026-10-25", "to": "2026-11-02"}).json()
        self.assertEqual([o["local_date"] for o in body["occurrences"]], ["2026-11-01", "2026-11-02"])

    def test_paused_task_not_in_history(self):
        self.daily.is_active = False
        self.daily.save()
        with frozen_now(utc(2026, 11, 16, 10)):
            body = parent_client(self.parent).get(self.url, {"from": "2026-11-14", "to": "2026-11-16"}).json()
        self.assertEqual(body["occurrences"], [])

    def test_other_room_404(self):
        other = create_room(create_parent("dad@example.com"))
        self.assertError(child_client(self.token).get(reverse("room-history", args=[other.id])), 404)
