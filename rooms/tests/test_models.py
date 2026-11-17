from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from config.test_utils import create_parent, create_room, join_room
from rooms.models import (
    JOIN_CODE_ALPHABET,
    JOIN_CODE_LENGTH,
    Child,
    Room,
    generate_child_token,
    generate_join_code,
    hash_child_token,
    validate_timezone,
)


class JoinCodeTests(TestCase):
    def test_code_uses_safe_alphabet(self):
        for _ in range(200):
            code = generate_join_code()
            self.assertEqual(len(code), JOIN_CODE_LENGTH)
            self.assertTrue(set(code) <= set(JOIN_CODE_ALPHABET), code)
        for forbidden in "0O1IL":
            self.assertNotIn(forbidden, JOIN_CODE_ALPHABET)

    def test_issue_code_sets_24h_expiry(self):
        room = create_room(create_parent())
        self.assertTrue(room.join_code_is_valid)
        delta = room.join_code_expires_at - timezone.now()
        self.assertGreater(delta, timedelta(hours=23, minutes=59))
        self.assertLessEqual(delta, timedelta(hours=24))

    def test_expired_code_is_invalid(self):
        room = create_room(create_parent())
        room.join_code_expires_at = timezone.now() - timedelta(seconds=1)
        self.assertFalse(room.join_code_is_valid)

    def test_cleared_code_is_invalid(self):
        room = create_room(create_parent())
        room.clear_join_code()
        self.assertFalse(room.join_code_is_valid)

    def test_active_codes_are_unique(self):
        parent = create_parent()
        a = create_room(parent)
        b = Room(parent=parent, name="B", timezone="UTC", join_code=a.join_code, join_code_expires_at=timezone.now())
        with self.assertRaises(IntegrityError):
            b.save()

    def test_many_rooms_without_code(self):
        parent = create_parent()
        for i in range(3):
            Room.objects.create(parent=parent, name=f"R{i}", timezone="UTC")
        self.assertEqual(Room.objects.filter(join_code__isnull=True).count(), 3)


class TimezoneTests(TestCase):
    def test_valid(self):
        validate_timezone("Europe/Rome")
        validate_timezone("America/New_York")
        validate_timezone("UTC")

    def test_invalid(self):
        for bad in ["Mars/Olympus", "", "CET+1", "../etc/passwd"]:
            with self.assertRaises(ValidationError, msg=bad):
                validate_timezone(bad)


class ChildTests(TestCase):
    def test_token_is_64_hex_and_only_hash_is_stored(self):
        token = generate_child_token()
        self.assertEqual(len(token), 64)
        int(token, 16)
        room = create_room(create_parent())
        raw = join_room(room)
        child = Child.objects.get()
        self.assertNotEqual(child.device_token_hash, raw)
        self.assertEqual(child.device_token_hash, hash_child_token(raw))

    def test_one_child_per_room(self):
        room = create_room(create_parent())
        join_room(room)
        with self.assertRaises(IntegrityError):
            Child.objects.create(room=room, display_name="B", device_token_hash="x" * 64)

    def test_deleting_room_deletes_child(self):
        room = create_room(create_parent())
        join_room(room)
        room.delete()
        self.assertEqual(Child.objects.count(), 0)
