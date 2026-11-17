from datetime import timedelta
from unittest import mock

from django.urls import reverse
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import Parent, ParentDevice
from config.test_utils import PASSWORD, HabitoTestCase, create_parent, parent_client


class RegisterTests(HabitoTestCase):
    url = reverse("auth-register")

    def test_register_returns_parent_and_tokens(self):
        response = self.client.post(
            self.url, {"email": "Mom@Example.com", "password": PASSWORD, "display_name": "Mom"}
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["parent"]["email"], "mom@example.com")
        self.assertEqual(body["parent"]["display_name"], "Mom")
        self.assertNotIn("password", body["parent"])
        self.assertIn("access", body)
        self.assertIn("refresh", body)
        self.assertEqual(Parent.objects.count(), 1)

    def test_duplicate_email_rejected(self):
        create_parent("mom@example.com")
        response = self.client.post(self.url, {"email": "MOM@example.com", "password": PASSWORD, "display_name": "X"})
        self.assertError(response, 400, "invalid_input")
        self.assertIn("email", response.json()["error"]["fields"])

    def test_short_password_rejected(self):
        response = self.client.post(self.url, {"email": "a@example.com", "password": "short", "display_name": "X"})
        self.assertError(response, 400, "invalid_input")
        self.assertIn("password", response.json()["error"]["fields"])

    def test_missing_fields_rejected(self):
        response = self.client.post(self.url, {"email": "a@example.com"})
        self.assertError(response, 400, "invalid_input")

    def test_access_token_works(self):
        response = self.client.post(self.url, {"email": "a@example.com", "password": PASSWORD, "display_name": "A"})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.json()['access']}")
        me = self.client.get(reverse("me"))
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["role"], "parent")


class LoginTests(HabitoTestCase):
    url = reverse("auth-login")

    def setUp(self):
        super().setUp()
        self.parent = create_parent()

    def test_login_ok(self):
        response = self.client.post(self.url, {"email": "MOM@example.com", "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["parent"]["id"], str(self.parent.id))
        self.assertIn("access", body)
        self.assertIn("refresh", body)

    def test_wrong_password(self):
        response = self.client.post(self.url, {"email": "mom@example.com", "password": "nope-nope"})
        self.assertError(response, 401, "invalid_credentials")

    def test_unknown_email(self):
        response = self.client.post(self.url, {"email": "ghost@example.com", "password": PASSWORD})
        self.assertError(response, 401, "invalid_credentials")

    def test_rate_limited_per_email(self):
        for _ in range(5):
            self.client.post(self.url, {"email": "mom@example.com", "password": "nope-nope"})
        blocked = self.client.post(self.url, {"email": "mom@example.com", "password": PASSWORD})
        self.assertError(blocked, 429, "too_many_attempts")
        # A different email is not affected.
        create_parent("dad@example.com")
        other = self.client.post(self.url, {"email": "dad@example.com", "password": PASSWORD})
        self.assertEqual(other.status_code, 200)


class RefreshTests(HabitoTestCase):
    url = reverse("auth-refresh")

    def setUp(self):
        super().setUp()
        self.parent = create_parent()
        self.refresh = str(RefreshToken.for_user(self.parent))

    def test_refresh_rotates(self):
        response = self.client.post(self.url, {"refresh": self.refresh})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIn("access", body)
        self.assertNotEqual(body["refresh"], self.refresh)

        # The old refresh token is now blacklisted.
        again = self.client.post(self.url, {"refresh": self.refresh})
        self.assertError(again, 401, "invalid_token")

        # The new one works.
        third = self.client.post(self.url, {"refresh": body["refresh"]})
        self.assertEqual(third.status_code, 200)

    def test_garbage_token(self):
        response = self.client.post(self.url, {"refresh": "not-a-token"})
        self.assertError(response, 401, "invalid_token")

    def test_expired_token(self):
        token = RefreshToken.for_user(self.parent)
        token.set_exp(lifetime=-timedelta(seconds=1))
        response = self.client.post(self.url, {"refresh": str(token)})
        self.assertError(response, 401, "invalid_token")

    def test_access_token_lifetime_is_15_minutes(self):
        from django.conf import settings

        self.assertEqual(settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"], timedelta(minutes=15))
        self.assertEqual(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"], timedelta(days=90))


class LogoutTests(HabitoTestCase):
    url = reverse("auth-logout")

    def setUp(self):
        super().setUp()
        self.parent = create_parent()
        self.client = parent_client(self.parent)
        self.refresh = str(RefreshToken.for_user(self.parent))

    def test_logout_blacklists_refresh_and_removes_device(self):
        ParentDevice.objects.create(parent=self.parent, apns_token="phone-1")
        ParentDevice.objects.create(parent=self.parent, apns_token="phone-2")
        response = self.client.post(self.url, {"refresh": self.refresh, "apns_token": "phone-1"})
        self.assertEqual(response.status_code, 204, response.content)
        self.assertEqual(BlacklistedToken.objects.count(), 1)
        self.assertEqual(list(ParentDevice.objects.values_list("apns_token", flat=True)), ["phone-2"])

        again = self.client.post(reverse("auth-refresh"), {"refresh": self.refresh})
        self.assertError(again, 401, "invalid_token")

    def test_logout_without_device_token(self):
        response = self.client.post(self.url, {"refresh": self.refresh})
        self.assertEqual(response.status_code, 204)

    def test_cannot_logout_someone_elses_token(self):
        other = create_parent("dad@example.com")
        other_refresh = str(RefreshToken.for_user(other))
        response = self.client.post(self.url, {"refresh": other_refresh})
        self.assertError(response, 400, "invalid_token")
        self.assertEqual(BlacklistedToken.objects.count(), 0)

    def test_requires_auth(self):
        response = self.client.__class__().post(self.url, {"refresh": self.refresh})
        self.assertError(response, 401, "unauthorized")


class DeleteAccountTests(HabitoTestCase):
    url = reverse("auth-account")

    def test_delete_removes_everything(self):
        parent = create_parent()
        ParentDevice.objects.create(parent=parent, apns_token="phone-1")
        RefreshToken.for_user(parent)
        client = parent_client(parent)
        response = client.delete(self.url)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(Parent.objects.count(), 0)
        self.assertEqual(ParentDevice.objects.count(), 0)
        self.assertEqual(OutstandingToken.objects.count(), 0)
        # The access token no longer works.
        self.assertError(client.get(reverse("me")), 401)

    def test_requires_auth(self):
        response = self.client.delete(self.url)
        self.assertError(response, 401)


class MeTests(HabitoTestCase):
    def test_parent(self):
        parent = create_parent()
        response = parent_client(parent).get(reverse("me"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"role": "parent", "parent": mock.ANY})
        self.assertEqual(response.json()["parent"]["email"], "mom@example.com")

    def test_anonymous(self):
        self.assertError(self.client.get(reverse("me")), 401, "unauthorized")

    def test_bad_token(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer nonsense")
        self.assertError(self.client.get(reverse("me")), 401, "unauthorized")


class DeviceTests(HabitoTestCase):
    def setUp(self):
        super().setUp()
        self.parent = create_parent()
        self.client = parent_client(self.parent)

    def test_register_device(self):
        response = self.client.post(reverse("device-list"), {"apns_token": "abc123"})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["apns_token"], "abc123")
        self.assertEqual(ParentDevice.objects.get().parent, self.parent)

    def test_register_same_device_twice_is_idempotent(self):
        first = self.client.post(reverse("device-list"), {"apns_token": "abc123"})
        second = self.client.post(reverse("device-list"), {"apns_token": "abc123"})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(ParentDevice.objects.count(), 1)

    def test_device_moves_to_new_parent(self):
        ParentDevice.objects.create(parent=create_parent("dad@example.com"), apns_token="abc123")
        response = self.client.post(reverse("device-list"), {"apns_token": "abc123"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ParentDevice.objects.get().parent, self.parent)

    def test_missing_token(self):
        self.assertError(self.client.post(reverse("device-list"), {}), 400, "invalid_input")

    def test_delete_own_device(self):
        device = ParentDevice.objects.create(parent=self.parent, apns_token="abc123")
        response = self.client.delete(reverse("device-detail", args=[device.id]))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(ParentDevice.objects.count(), 0)

    def test_delete_other_parents_device_is_404(self):
        device = ParentDevice.objects.create(parent=create_parent("dad@example.com"), apns_token="abc123")
        response = self.client.delete(reverse("device-detail", args=[device.id]))
        self.assertError(response, 404, "not_found")
        self.assertEqual(ParentDevice.objects.count(), 1)

    def test_requires_parent(self):
        response = self.client.__class__().post(reverse("device-list"), {"apns_token": "abc123"})
        self.assertError(response, 401)
