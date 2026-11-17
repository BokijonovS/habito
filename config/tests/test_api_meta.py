"""Error format, OpenAPI docs and other cross-cutting behaviour."""

from django.test import override_settings
from django.urls import reverse

from config.test_utils import HabitoTestCase, create_parent, parent_client


class ErrorFormatTests(HabitoTestCase):
    def test_validation_error_shape(self):
        response = self.client.post(reverse("auth-register"), {"email": "nope"})
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(set(body), {"error"})
        self.assertEqual(body["error"]["code"], "invalid_input")
        self.assertIsInstance(body["error"]["message"], str)
        self.assertIn("email", body["error"]["fields"])

    def test_unauthorized_shape(self):
        response = self.client.get(reverse("me"))
        self.assertEqual(response.json(), {"error": {"code": "unauthorized", "message": "Missing or expired token."}})
        self.assertTrue(response.headers.get("WWW-Authenticate"))

    def test_method_not_allowed(self):
        response = parent_client(create_parent()).put(reverse("me"), {})
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()["error"]["code"], "method_not_allowed")

    @override_settings(DEBUG=False)
    def test_unknown_url_is_json_404(self):
        response = self.client.get("/api/v1/nothing-here")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_invalid_json_body(self):
        response = self.client.generic(
            "POST", reverse("auth-login"), data="{not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_input")


class OpenApiTests(HabitoTestCase):
    def test_docs_page(self):
        response = self.client.get("/api/docs/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"swagger", response.content.lower())

    def test_schema_lists_every_spec_endpoint(self):
        response = self.client.get("/api/schema/", HTTP_ACCEPT="application/vnd.oai.openapi+json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        paths = response.json()["paths"]
        expected = {
            ("post", "/api/v1/auth/register"),
            ("post", "/api/v1/auth/login"),
            ("post", "/api/v1/auth/refresh"),
            ("post", "/api/v1/auth/logout"),
            ("delete", "/api/v1/auth/account"),
            ("get", "/api/v1/me"),
            ("post", "/api/v1/devices"),
            ("delete", "/api/v1/devices/{device_id}"),
            ("post", "/api/v1/rooms"),
            ("get", "/api/v1/rooms"),
            ("get", "/api/v1/rooms/{room_id}"),
            ("patch", "/api/v1/rooms/{room_id}"),
            ("delete", "/api/v1/rooms/{room_id}"),
            ("post", "/api/v1/rooms/{room_id}/code"),
            ("delete", "/api/v1/rooms/{room_id}/child"),
            ("post", "/api/v1/join"),
            ("get", "/api/v1/rooms/{room_id}/tasks"),
            ("post", "/api/v1/rooms/{room_id}/tasks"),
            ("patch", "/api/v1/tasks/{task_id}"),
            ("delete", "/api/v1/tasks/{task_id}"),
            ("get", "/api/v1/rooms/{room_id}/today"),
            ("get", "/api/v1/rooms/{room_id}/completed"),
            ("get", "/api/v1/rooms/{room_id}/history"),
            ("post", "/api/v1/tasks/{task_id}/done"),
            ("delete", "/api/v1/tasks/{task_id}/done"),
        }
        self.assertEqual(len(expected), 25)
        for method, path in expected:
            self.assertIn(path, paths, path)
            self.assertIn(method, paths[path], f"{method} {path}")
