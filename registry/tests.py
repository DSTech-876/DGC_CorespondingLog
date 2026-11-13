import os
import socket
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
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
    """Verifies the DGC logo renders on key pages."""

    def test_login_page_renders_logo(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "img/dgc.png")

    def test_dashboard_renders_logo_for_authenticated_user(self):
        user = get_user_model().objects.create_user("staff2", password="pass12345!")
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "img/dgc.png")


class SeedAdminCommandTests(TestCase):
    """Covers non-interactive seeding of the first administrator account."""

    def test_creates_superuser_with_given_credentials(self):
        out = StringIO()
        call_command("seed_admin", username="root", email="root@example.com",
                     password="Seeded-pass-1", stdout=out)
        user = get_user_model().objects.get(username="root")
        self.assertTrue(user.is_superuser and user.is_staff and user.is_active)
        self.assertEqual(user.email, "root@example.com")
        self.assertTrue(user.check_password("Seeded-pass-1"))

    def test_generates_password_when_none_supplied(self):
        out = StringIO()
        call_command("seed_admin", username="gen", stdout=out)
        self.assertIn("Generated password:", out.getvalue())

    def test_rerun_is_idempotent_and_restores_privileges(self):
        user = get_user_model().objects.create_user("admin", password="Original-pass-1")
        call_command("seed_admin", username="admin", stdout=StringIO())
        user.refresh_from_db()
        self.assertTrue(user.is_superuser and user.is_staff)
        self.assertTrue(user.check_password("Original-pass-1"))
        self.assertEqual(get_user_model().objects.filter(username="admin").count(), 1)

    def test_reset_password_flag_replaces_password(self):
        get_user_model().objects.create_superuser("admin2", password="Original-pass-1")
        call_command("seed_admin", username="admin2", password="New-pass-12345",
                     reset_password=True, stdout=StringIO())
        user = get_user_model().objects.get(username="admin2")
        self.assertTrue(user.check_password("New-pass-12345"))

    def test_uses_environment_variables(self):
        with mock.patch.dict(os.environ, {"DJANGO_SUPERUSER_USERNAME": "envadmin",
                                          "DJANGO_SUPERUSER_PASSWORD": "Env-pass-12345"}):
            call_command("seed_admin", stdout=StringIO())
        self.assertTrue(get_user_model().objects.get(username="envadmin").check_password("Env-pass-12345"))


class HostValidationTests(TestCase):
    """Guards the LAN "Bad Request (400)" fix: the server's own names resolve."""

    def test_local_hostname_is_allowed(self):
        response = self.client.get(reverse("login"), headers={"host": socket.gethostname()})
        self.assertEqual(response.status_code, 200)

    def test_loopback_host_is_allowed(self):
        response = self.client.get(reverse("login"), headers={"host": "127.0.0.1:8086"})
        self.assertEqual(response.status_code, 200)

    def test_unknown_host_is_still_rejected(self):
        response = self.client.get(reverse("login"), headers={"host": "evil.example.com"})
        self.assertEqual(response.status_code, 400)
