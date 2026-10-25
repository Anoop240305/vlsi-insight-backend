from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient


class AccountApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="engineer", password="test-password-123", email=""
        )
        self.client = APIClient()

    def test_sign_in_saves_email_and_returns_profile(self):
        response = self.client.post("/api/token/", {
            "username": "engineer",
            "password": "test-password-123",
            "email": "engineer@example.com",
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("token", response.json())
        self.assertEqual(response.json()["user"]["email"], "engineer@example.com")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "engineer@example.com")

        self.client.credentials(HTTP_AUTHORIZATION=f"Token {response.json()['token']}")
        profile = self.client.get("/api/profile/")
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json()["username"], "engineer")
        self.assertEqual(profile.json()["email"], "engineer@example.com")

    def test_sign_in_rejects_invalid_email(self):
        response = self.client.post("/api/token/", {
            "username": "engineer",
            "password": "test-password-123",
            "email": "not-an-email",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json()["detail"].lower())

    def test_token_sign_in_without_email_remains_supported(self):
        response = self.client.post("/api/token/", {
            "username": "engineer",
            "password": "test-password-123",
        }, format="json")
        self.assertEqual(response.status_code, 200)
