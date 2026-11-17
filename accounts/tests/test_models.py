from django.db import IntegrityError
from django.test import TestCase

from accounts.models import Parent, ParentDevice


class ParentModelTests(TestCase):
    def test_email_is_stored_lowercase(self):
        parent = Parent.objects.create_user(email="Mom@Example.COM", password="password123", display_name="Mom")
        self.assertEqual(parent.email, "mom@example.com")

    def test_password_is_hashed(self):
        parent = Parent.objects.create_user(email="a@example.com", password="password123", display_name="A")
        self.assertNotEqual(parent.password, "password123")
        self.assertTrue(parent.check_password("password123"))

    def test_email_unique(self):
        Parent.objects.create_user(email="a@example.com", password="password123", display_name="A")
        with self.assertRaises(IntegrityError):
            Parent.objects.create_user(email="A@example.com", password="password123", display_name="B")

    def test_roles(self):
        parent = Parent.objects.create_user(email="a@example.com", password="password123", display_name="A")
        self.assertTrue(parent.is_parent)
        self.assertFalse(parent.is_child)


class ParentDeviceModelTests(TestCase):
    def test_token_unique_across_parents(self):
        a = Parent.objects.create_user(email="a@example.com", password="password123", display_name="A")
        b = Parent.objects.create_user(email="b@example.com", password="password123", display_name="B")
        ParentDevice.objects.create(parent=a, apns_token="tok")
        with self.assertRaises(IntegrityError):
            ParentDevice.objects.create(parent=b, apns_token="tok")

    def test_deleting_parent_deletes_devices(self):
        a = Parent.objects.create_user(email="a@example.com", password="password123", display_name="A")
        ParentDevice.objects.create(parent=a, apns_token="tok")
        a.delete()
        self.assertEqual(ParentDevice.objects.count(), 0)
