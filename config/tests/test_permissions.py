"""
The Permissions table from SPEC.md, one check per cell.

Expected outcomes: "ok" (2xx), 401 (not signed in), 403 (wrong role) or
404 (a room or task the caller does not belong to).
"""

from datetime import time

from django.urls import reverse
from rest_framework.test import APIClient

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

NOW = utc(2026, 11, 16, 10)  # 15:00 in Asia/Tashkent


class PermissionMatrixTests(HabitoTestCase):
    def setUp(self):
        super().setUp()
        self.parent = create_parent("mom@example.com")
        self.room = create_room(self.parent, tz="Asia/Tashkent")
        self.task = create_task(self.room, "Brush teeth", "daily", created_at=utc(2026, 11, 1))
        self.child_token = join_room(self.room, "Ali")

        self.stranger = create_parent("stranger@example.com")
        self.other_room = create_room(self.stranger, tz="Asia/Tashkent")
        self.other_task = create_task(self.other_room, "Theirs", "hourly", window_start=time(9), window_end=time(20))
        join_room(self.other_room, "Bo")

        self.clients = {
            "parent": parent_client(self.parent),
            "child": child_client(self.child_token),
            "anon": APIClient(),
        }

    def call(self, who, method, url, data=None):
        with frozen_now(NOW), self.captureOnCommitCallbacks(execute=True):
            return getattr(self.clients[who], method)(url, data)

    def check(self, who, method, url, expected, data=None):
        response = self.call(who, method, url, data)
        if expected == "ok":
            self.assertIn(response.status_code // 100, (2,), f"{who} {method} {url}: {response.content}")
        else:
            self.assertEqual(response.status_code, expected, f"{who} {method} {url}: {response.content}")
            self.assertIn("error", response.json())

    # --- own room: parent ok, child ok or forbidden, anon 401 --------------

    def test_register_device(self):
        url = reverse("device-list")
        self.check("parent", "post", url, "ok", {"apns_token": "t"})
        self.check("child", "post", url, 403, {"apns_token": "t"})
        self.check("anon", "post", url, 401, {"apns_token": "t"})

    def test_create_room(self):
        url = reverse("room-list")
        data = {"name": "New", "timezone": "UTC"}
        self.check("parent", "post", url, "ok", data)
        self.check("child", "post", url, 403, data)
        self.check("anon", "post", url, 401, data)

    def test_view_room(self):
        url = reverse("room-detail", args=[self.room.id])
        self.check("parent", "get", url, "ok")
        self.check("child", "get", url, "ok")
        self.check("anon", "get", url, 401)
        other = reverse("room-detail", args=[self.other_room.id])
        self.check("parent", "get", other, 404)
        self.check("child", "get", other, 404)

    def test_rename_room(self):
        url = reverse("room-detail", args=[self.room.id])
        self.check("child", "patch", url, 403, {"name": "X"})
        self.check("anon", "patch", url, 401, {"name": "X"})
        self.check("parent", "patch", reverse("room-detail", args=[self.other_room.id]), 404, {"name": "X"})
        self.check("parent", "patch", url, "ok", {"name": "X"})

    def test_new_code_and_remove_child(self):
        for name in ("room-code", "room-child"):
            method = "post" if name == "room-code" else "delete"
            self.check("child", method, reverse(name, args=[self.room.id]), 403)
            self.check("anon", method, reverse(name, args=[self.room.id]), 401)
            self.check("parent", method, reverse(name, args=[self.other_room.id]), 404)
        self.check("parent", "delete", reverse("room-child", args=[self.room.id]), "ok")
        self.check("parent", "post", reverse("room-code", args=[self.room.id]), "ok")

    def test_join(self):
        self.check("anon", "post", reverse("join"), 404, {"code": "ZZZZZZ", "display_name": "A"})
        new_room = create_room(self.parent, "Second", tz="UTC")
        self.check("anon", "post", reverse("join"), "ok", {"code": new_room.join_code, "display_name": "A"})

    def test_create_edit_delete_tasks(self):
        create_url = reverse("room-tasks", args=[self.room.id])
        data = {"title": "T", "repeat": "daily"}
        self.check("child", "post", create_url, 403, data)
        self.check("anon", "post", create_url, 401, data)
        self.check("parent", "post", reverse("room-tasks", args=[self.other_room.id]), 404, data)
        self.check("parent", "post", create_url, "ok", data)

        edit_url = reverse("task-detail", args=[self.task.id])
        self.check("child", "patch", edit_url, 403, {"title": "X"})
        self.check("anon", "patch", edit_url, 401, {"title": "X"})
        self.check("parent", "patch", reverse("task-detail", args=[self.other_task.id]), 404, {"title": "X"})
        self.check("parent", "patch", edit_url, "ok", {"title": "X"})

        self.check("child", "delete", edit_url, 403)
        self.check("parent", "delete", reverse("task-detail", args=[self.other_task.id]), 404)
        self.check("parent", "delete", edit_url, "ok")

    def test_view_tasks_and_today(self):
        for name in ("room-tasks", "room-today"):
            self.check("parent", "get", reverse(name, args=[self.room.id]), "ok")
            self.check("child", "get", reverse(name, args=[self.room.id]), "ok")
            self.check("anon", "get", reverse(name, args=[self.room.id]), 401)
            self.check("parent", "get", reverse(name, args=[self.other_room.id]), 404)
            self.check("child", "get", reverse(name, args=[self.other_room.id]), 404)

    def test_mark_done_and_undo(self):
        url = reverse("task-done", args=[self.task.id])
        self.check("parent", "post", url, 403)
        self.check("anon", "post", url, 401)
        self.check("child", "post", reverse("task-done", args=[self.other_task.id]), 404)
        self.check("child", "post", url, "ok")
        self.check("parent", "delete", url, 403)
        self.check("child", "delete", reverse("task-done", args=[self.other_task.id]), 404)
        self.check("child", "delete", url, "ok")

    def test_completed_and_history(self):
        for name in ("room-completed", "room-history"):
            self.check("parent", "get", reverse(name, args=[self.room.id]), "ok")
            self.check("child", "get", reverse(name, args=[self.room.id]), "ok")
            self.check("anon", "get", reverse(name, args=[self.room.id]), 401)
            self.check("parent", "get", reverse(name, args=[self.other_room.id]), 404)
            self.check("child", "get", reverse(name, args=[self.other_room.id]), 404)

    def test_delete_account(self):
        url = reverse("auth-account")
        self.check("child", "delete", url, 403)
        self.check("anon", "delete", url, 401)
        self.check("parent", "delete", url, "ok")

    def test_room_of_other_parent_is_404_not_403(self):
        response = self.call("parent", "get", reverse("room-detail", args=[self.other_room.id]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")
