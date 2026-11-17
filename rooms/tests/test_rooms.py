from django.urls import reverse

from config.test_utils import HabitoTestCase, child_client, create_parent, create_room, join_room, parent_client
from rooms.models import Child, Room


class CreateAndListRoomTests(HabitoTestCase):
    def setUp(self):
        super().setUp()
        self.parent = create_parent()
        self.client = parent_client(self.parent)

    def test_create_room_returns_code(self):
        response = self.client.post(reverse("room-list"), {"name": "Ali's room", "timezone": "Europe/Rome"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["name"], "Ali's room")
        self.assertEqual(body["timezone"], "Europe/Rome")
        self.assertIsNone(body["child"])
        self.assertEqual(len(body["join_code"]), 6)
        self.assertIsNotNone(body["join_code_expires_at"])
        self.assertEqual(Room.objects.get().parent, self.parent)

    def test_create_room_bad_timezone(self):
        response = self.client.post(reverse("room-list"), {"name": "X", "timezone": "Mars/Olympus"})
        self.assertError(response, 400, "invalid_input")
        self.assertIn("timezone", response.json()["error"]["fields"])

    def test_create_room_name_too_long(self):
        response = self.client.post(reverse("room-list"), {"name": "x" * 51, "timezone": "UTC"})
        self.assertError(response, 400, "invalid_input")

    def test_list_only_own_rooms(self):
        create_room(self.parent, "Mine")
        create_room(create_parent("dad@example.com"), "Theirs")
        response = self.client.get(reverse("room-list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["name"] for r in response.json()], ["Mine"])

    def test_child_cannot_create_or_list_rooms(self):
        token = join_room(create_room(self.parent))
        client = child_client(token)
        self.assertError(client.post(reverse("room-list"), {"name": "X", "timezone": "UTC"}), 403)
        self.assertError(client.get(reverse("room-list")), 403)

    def test_anonymous(self):
        self.assertError(self.client.__class__().get(reverse("room-list")), 401)


class RoomDetailTests(HabitoTestCase):
    def setUp(self):
        super().setUp()
        self.parent = create_parent()
        self.room = create_room(self.parent)
        self.client = parent_client(self.parent)
        self.other = create_parent("dad@example.com")
        self.other_room = create_room(self.other, "Other")

    def url(self, room=None):
        return reverse("room-detail", args=[(room or self.room).id])

    def test_parent_sees_own_room_with_code(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["join_code"], self.room.join_code)

    def test_parent_sees_child_once_joined(self):
        join_room(self.room, "Ali")
        body = self.client.get(self.url()).json()
        self.assertEqual(body["child"]["display_name"], "Ali")
        self.assertIsNone(body["join_code"])

    def test_other_parents_room_is_404(self):
        self.assertError(self.client.get(self.url(self.other_room)), 404, "not_found")
        self.assertError(self.client.patch(self.url(self.other_room), {"name": "Hacked"}), 404, "not_found")
        self.assertError(self.client.delete(self.url(self.other_room)), 404, "not_found")
        self.assertEqual(Room.objects.get(pk=self.other_room.pk).name, "Other")

    def test_unknown_room_is_404(self):
        self.assertError(self.client.get(reverse("room-detail", args=["00000000-0000-4000-8000-000000000000"])), 404)

    def test_child_sees_own_room_without_code(self):
        token = join_room(self.room)
        response = child_client(token).get(self.url())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("join_code", body)
        self.assertEqual(body["child"]["display_name"], "Ali")

    def test_child_cannot_see_other_room(self):
        token = join_room(self.room)
        self.assertError(child_client(token).get(self.url(self.other_room)), 404, "not_found")

    def test_child_cannot_rename_or_delete(self):
        token = join_room(self.room)
        client = child_client(token)
        self.assertError(client.patch(self.url(), {"name": "X"}), 403, "forbidden")
        self.assertError(client.delete(self.url()), 403, "forbidden")
        self.assertEqual(Room.objects.count(), 2)

    def test_rename(self):
        response = self.client.patch(self.url(), {"name": "New name"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["name"], "New name")
        self.room.refresh_from_db()
        self.assertEqual(self.room.timezone, "Europe/Rome")

    def test_change_timezone(self):
        response = self.client.patch(self.url(), {"timezone": "Asia/Tashkent"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["timezone"], "Asia/Tashkent")

    def test_bad_timezone(self):
        self.assertError(self.client.patch(self.url(), {"timezone": "Nope"}), 400, "invalid_input")

    def test_delete(self):
        join_room(self.room)
        response = self.client.delete(self.url())
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Room.objects.filter(pk=self.room.pk).exists())
        self.assertEqual(Child.objects.count(), 0)


class RoomCodeTests(HabitoTestCase):
    def setUp(self):
        super().setUp()
        self.parent = create_parent()
        self.room = create_room(self.parent)
        self.client = parent_client(self.parent)

    def test_new_code_signs_out_child(self):
        token = join_room(self.room)
        old_client = child_client(token)
        self.assertEqual(old_client.get(reverse("me")).status_code, 200)

        response = self.client.post(reverse("room-code", args=[self.room.id]))
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(len(body["join_code"]), 6)
        self.assertEqual(Child.objects.count(), 0)
        self.assertError(old_client.get(reverse("me")), 401)

    def test_new_code_replaces_old(self):
        old = self.room.join_code
        new = self.client.post(reverse("room-code", args=[self.room.id])).json()["join_code"]
        self.assertNotEqual(old, new)
        self.assertError(self.client.__class__().post(reverse("join"), {"code": old, "display_name": "A"}), 404)

    def test_other_room_404(self):
        other = create_room(create_parent("dad@example.com"))
        self.assertError(self.client.post(reverse("room-code", args=[other.id])), 404)

    def test_child_forbidden(self):
        token = join_room(self.room)
        self.assertError(child_client(token).post(reverse("room-code", args=[self.room.id])), 403)


class RemoveChildTests(HabitoTestCase):
    def setUp(self):
        super().setUp()
        self.parent = create_parent()
        self.room = create_room(self.parent)
        self.client = parent_client(self.parent)

    def test_remove_child(self):
        token = join_room(self.room)
        response = self.client.delete(reverse("room-child", args=[self.room.id]))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(Child.objects.count(), 0)
        self.assertError(child_client(token).get(reverse("me")), 401)

    def test_no_child_404(self):
        self.assertError(self.client.delete(reverse("room-child", args=[self.room.id])), 404)

    def test_other_room_404(self):
        other = create_room(create_parent("dad@example.com"))
        join_room(other)
        self.assertError(self.client.delete(reverse("room-child", args=[other.id])), 404)
        self.assertEqual(Child.objects.count(), 1)
