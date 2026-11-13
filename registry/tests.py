from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class DashboardAccessTests(TestCase):
    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_authenticated_user_sees_dashboard(self):
        user = get_user_model().objects.create_user("staff", password="pass12345!")
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard")


class LogoTemplateTests(TestCase):
    """Verifies the logo introduced for branding renders on key pages."""

    def test_login_page_renders_logo(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "img/logo.svg")

    def test_dashboard_renders_logo_for_authenticated_user(self):
        user = get_user_model().objects.create_user("staff2", password="pass12345!")
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "img/logo.svg")
