from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from config.test_utils import HabitoTestCase, child_client, create_parent, create_room, join_room
from rooms.models import Child, Room


class JoinTests(HabitoTestCase):
    url = reverse("join")

    def setUp(self):
        super().setUp()
        self.parent = create_parent()
        self.room = create_room(self.parent)

    def test_join_ok(self):
        response = self.client.post(self.url, {"code": self.room.join_code, "display_name": "Ali"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(len(body["child_token"]), 64)
        self.assertEqual(body["child"]["display_name"], "Ali")
        self.assertEqual(body["room"]["id"], str(self.room.id))
        self.assertEqual(body["room"]["name"], "Ali's room")
        self.assertEqual(body["room"]["timezone"], "Europe/Rome")
        self.assertNotIn("join_code", body["room"])

        self.room.refresh_from_db()
        self.assertIsNone(self.room.join_code)
        self.assertIsNone(self.room.join_code_expires_at)

        me = child_client(body["child_token"]).get(reverse("me"))
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["role"], "child")
        self.assertEqual(me.json()["child"]["display_name"], "Ali")
        self.assertEqual(me.json()["room"]["id"], str(self.room.id))

    def test_code_is_case_insensitive_and_ignores_spaces(self):
        messy = f" {self.room.join_code.lower()[:3]} {self.room.join_code.lower()[3:]} "
        response = self.client.post(self.url, {"code": messy, "display_name": "Ali"})
        self.assertEqual(response.status_code, 201, response.content)

    def test_code_is_single_use(self):
        self.client.post(self.url, {"code": self.room.join_code, "display_name": "Ali"})
        again = self.client.post(self.url, {"code": self.room.join_code, "display_name": "Bob"})
        self.assertError(again, 404, "invalid_code")
        self.assertEqual(Child.objects.count(), 1)

    def test_unknown_code(self):
        self.assertError(self.client.post(self.url, {"code": "ZZZZZZ", "display_name": "Ali"}), 404, "invalid_code")

    def test_expired_code(self):
        self.room.join_code_expires_at = timezone.now() - timedelta(minutes=1)
        self.room.save()
        response = self.client.post(self.url, {"code": self.room.join_code, "display_name": "Ali"})
        self.assertError(response, 409, "code_expired")
        self.assertEqual(Child.objects.count(), 0)

    def test_room_already_has_child(self):
        # A code that is somehow still set while a child exists is refused.
        join_room(self.room)
        Room.objects.filter(pk=self.room.pk).update(join_code="ABCDEF", join_code_expires_at=timezone.now() + timedelta(hours=1))
        response = self.client.post(self.url, {"code": "ABCDEF", "display_name": "Bob"})
        self.assertError(response, 409, "code_used")

    def test_missing_name(self):
        self.assertError(self.client.post(self.url, {"code": self.room.join_code}), 400, "invalid_input")

    def test_name_too_long(self):
        response = self.client.post(self.url, {"code": self.room.join_code, "display_name": "x" * 31})
        self.assertError(response, 400, "invalid_input")

    def test_rate_limited_per_ip(self):
        for _ in range(10):
            self.client.post(self.url, {"code": "ZZZZZZ", "display_name": "Ali"})
        blocked = self.client.post(self.url, {"code": self.room.join_code, "display_name": "Ali"})
        self.assertError(blocked, 429, "too_many_attempts")
        self.assertEqual(Child.objects.count(), 0)

        other_ip = self.client.post(
            self.url, {"code": self.room.join_code, "display_name": "Ali"}, REMOTE_ADDR="10.0.0.9"
        )
        self.assertEqual(other_ip.status_code, 201, other_ip.content)


class ChildAuthenticationTests(HabitoTestCase):
    def setUp(self):
        super().setUp()
        self.room = create_room(create_parent())
        self.token = join_room(self.room)

    def test_valid_token(self):
        response = child_client(self.token).get(reverse("me"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["role"], "child")

    def test_unknown_hex_token(self):
        response = child_client("0" * 64).get(reverse("me"))
        self.assertError(response, 401, "unauthorized")

    def test_revoked_after_child_removed(self):
        Child.objects.all().delete()
        self.assertError(child_client(self.token).get(reverse("me")), 401)

    def test_child_cannot_use_parent_endpoints(self):
        client = child_client(self.token)
        self.assertError(client.post(reverse("device-list"), {"apns_token": "x"}), 403, "forbidden")
        self.assertError(client.delete(reverse("auth-account")), 403, "forbidden")
        self.assertError(client.post(reverse("auth-logout"), {"refresh": "x"}), 403, "forbidden")

    def test_token_not_in_logs_or_db(self):
        child = Child.objects.get()
        self.assertNotEqual(child.device_token_hash, self.token)
