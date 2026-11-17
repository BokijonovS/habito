from datetime import date

from django.urls import reverse

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
from tasks.models import Completion, Task


class TaskCreateTests(HabitoTestCase):
    def setUp(self):
        super().setUp()
        self.parent = create_parent()
        self.room = create_room(self.parent, tz="Asia/Tashkent")
        self.client = parent_client(self.parent)
        self.url = reverse("room-tasks", args=[self.room.id])

    def test_create_daily(self):
        response = self.client.post(self.url, {"title": "Brush teeth", "repeat": "daily", "due_time": "08:00"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["title"], "Brush teeth")
        self.assertEqual(body["repeat"], "daily")
        self.assertEqual(body["due_time"], "08:00:00")
        self.assertTrue(body["is_active"])
        self.assertEqual(body["room"], str(self.room.id))

    def test_create_hourly_with_window(self):
        response = self.client.post(
            self.url, {"title": "Drink water", "repeat": "hourly", "window_start": "09:00", "window_end": "20:00"}
        )
        self.assertEqual(response.status_code, 201, response.content)

    def test_create_hourly_without_window(self):
        response = self.client.post(self.url, {"title": "Drink water", "repeat": "hourly"})
        self.assertEqual(response.status_code, 201, response.content)

    def test_hourly_window_must_be_complete_and_ordered(self):
        r = self.client.post(self.url, {"title": "X", "repeat": "hourly", "window_start": "09:00"})
        self.assertError(r, 400, "invalid_input")
        r = self.client.post(self.url, {"title": "X", "repeat": "hourly", "window_start": "20:00", "window_end": "09:00"})
        self.assertError(r, 400, "invalid_input")

    def test_create_once_with_due_date(self):
        response = self.client.post(self.url, {"title": "Dentist", "repeat": "once", "due_date": "2026-12-01"})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["due_date"], "2026-12-01")

    def test_create_once_defaults_due_date_to_local_today(self):
        # 22:00 UTC on Nov 16 is already Nov 17 in Tashkent.
        with frozen_now(utc(2026, 11, 16, 22)):
            response = self.client.post(self.url, {"title": "Dentist", "repeat": "once"})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["due_date"], "2026-11-17")

    def test_fields_for_other_repeat_rejected(self):
        r = self.client.post(self.url, {"title": "X", "repeat": "daily", "due_date": "2026-12-01"})
        self.assertError(r, 400, "invalid_input")
        self.assertIn("due_date", r.json()["error"]["fields"])
        r = self.client.post(self.url, {"title": "X", "repeat": "once", "window_start": "09:00", "window_end": "10:00"})
        self.assertError(r, 400, "invalid_input")

    def test_validation(self):
        self.assertError(self.client.post(self.url, {"title": "X"}), 400, "invalid_input")
        self.assertError(self.client.post(self.url, {"title": "X", "repeat": "weekly"}), 400, "invalid_input")
        self.assertError(self.client.post(self.url, {"title": "x" * 81, "repeat": "daily"}), 400, "invalid_input")
        self.assertError(
            self.client.post(self.url, {"title": "X", "repeat": "daily", "description": "d" * 501}), 400, "invalid_input"
        )

    def test_child_cannot_create(self):
        token = join_room(self.room)
        response = child_client(token).post(self.url, {"title": "Ice cream", "repeat": "daily"})
        self.assertError(response, 403, "forbidden")
        self.assertEqual(Task.objects.count(), 0)

    def test_other_parent_room_404(self):
        other = create_room(create_parent("dad@example.com"))
        response = self.client.post(reverse("room-tasks", args=[other.id]), {"title": "X", "repeat": "daily"})
        self.assertError(response, 404, "not_found")


class TaskListTests(HabitoTestCase):
    def setUp(self):
        super().setUp()
        self.parent = create_parent()
        self.room = create_room(self.parent)
        create_task(self.room, "A", "daily")
        create_task(self.room, "B", "hourly", is_active=False)
        self.other = create_room(create_parent("dad@example.com"))
        create_task(self.other, "Other", "daily")

    def test_parent_lists_all_including_paused(self):
        response = parent_client(self.parent).get(reverse("room-tasks", args=[self.room.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([t["title"] for t in response.json()], ["A", "B"])

    def test_child_lists_own_room(self):
        token = join_room(self.room)
        response = child_client(token).get(reverse("room-tasks", args=[self.room.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 2)

    def test_other_room_404_for_both_roles(self):
        self.assertError(parent_client(self.parent).get(reverse("room-tasks", args=[self.other.id])), 404)
        token = join_room(self.room)
        self.assertError(child_client(token).get(reverse("room-tasks", args=[self.other.id])), 404)

    def test_anonymous(self):
        self.assertError(self.client.get(reverse("room-tasks", args=[self.room.id])), 401)


class TaskEditDeleteTests(HabitoTestCase):
    def setUp(self):
        super().setUp()
        self.parent = create_parent()
        self.room = create_room(self.parent, tz="Asia/Tashkent")
        self.task = create_task(self.room, "Brush teeth", "daily")
        self.client = parent_client(self.parent)
        self.url = reverse("task-detail", args=[self.task.id])

    def test_rename(self):
        response = self.client.patch(self.url, {"title": "Brush teeth well"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["title"], "Brush teeth well")

    def test_pause_and_resume(self):
        self.assertFalse(self.client.patch(self.url, {"is_active": False}).json()["is_active"])
        self.assertTrue(self.client.patch(self.url, {"is_active": True}).json()["is_active"])

    def test_change_repeat_validates_against_existing_fields(self):
        create_task(self.room, "W", "hourly")
        hourly = Task.objects.get(title="W")
        response = self.client.patch(reverse("task-detail", args=[hourly.id]), {"repeat": "daily"})
        self.assertEqual(response.status_code, 200, response.content)
        # A daily task cannot take a window.
        response = self.client.patch(reverse("task-detail", args=[hourly.id]), {"window_start": "09:00", "window_end": "10:00"})
        self.assertError(response, 400, "invalid_input")

    def test_switch_to_once_defaults_due_date(self):
        with frozen_now(utc(2026, 11, 16, 22)):
            response = self.client.patch(self.url, {"repeat": "once", "due_time": None})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["due_date"], "2026-11-17")

    def test_delete_keeps_completions(self):
        Completion.objects.create(
            task=self.task,
            room=self.room,
            task_title="Brush teeth",
            period_start=utc(2026, 11, 15, 19),
            local_date=date(2026, 11, 16),
            done_at=utc(2026, 11, 16, 3),
        )
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(Task.objects.count(), 0)
        completion = Completion.objects.get()
        self.assertIsNone(completion.task)
        self.assertEqual(completion.task_title, "Brush teeth")

    def test_other_parents_task_404(self):
        other_task = create_task(create_room(create_parent("dad@example.com")), "Theirs", "daily")
        url = reverse("task-detail", args=[other_task.id])
        self.assertError(self.client.patch(url, {"title": "Hacked"}), 404, "not_found")
        self.assertError(self.client.delete(url), 404, "not_found")
        self.assertEqual(Task.objects.get(pk=other_task.pk).title, "Theirs")

    def test_child_forbidden(self):
        client = child_client(join_room(self.room))
        self.assertError(client.patch(self.url, {"title": "X"}), 403, "forbidden")
        self.assertError(client.delete(self.url), 403, "forbidden")
        self.assertEqual(Task.objects.count(), 1)
