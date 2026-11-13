import csv
import os
import re
import socket
import tempfile
from datetime import date, datetime
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from registry.importer import import_records, parse_rows, rows_from_stream
from registry.models import AuditLog, Correspondence, UserProfile
from registry.roles import apply_role


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


class UserProfileSignalTests(TestCase):
    """Every account gets a profile, with superusers defaulting to admin role."""

    def test_profile_created_for_new_user(self):
        user = get_user_model().objects.create_user("profiled", password="pass12345!")
        self.assertEqual(user.profile.role, UserProfile.Role.OFFICER)
        self.assertFalse(user.profile.must_change_password)

    def test_superuser_profile_defaults_to_admin_role(self):
        user = get_user_model().objects.create_superuser("rootadmin", password="pass12345!")
        self.assertEqual(user.profile.role, UserProfile.Role.ADMIN)


class RoleTests(TestCase):
    """apply_role keeps auth flags and group membership in step with the role."""

    def test_officer_role_grants_register_permissions(self):
        user = get_user_model().objects.create_user("officer1", password="pass12345!")
        apply_role(user, UserProfile.Role.OFFICER)
        user = get_user_model().objects.get(pk=user.pk)
        self.assertTrue(user.has_perm("registry.add_correspondence"))
        self.assertTrue(user.has_perm("registry.export_correspondence"))
        self.assertFalse(user.is_staff)

    def test_viewer_role_is_read_only(self):
        user = get_user_model().objects.create_user("viewer1", password="pass12345!")
        apply_role(user, UserProfile.Role.VIEWER)
        user = get_user_model().objects.get(pk=user.pk)
        self.assertTrue(user.has_perm("registry.view_correspondence"))
        self.assertFalse(user.has_perm("registry.add_correspondence"))

    def test_changing_role_replaces_previous_role_group(self):
        user = get_user_model().objects.create_user("switcher", password="pass12345!")
        apply_role(user, UserProfile.Role.OFFICER)
        apply_role(user, UserProfile.Role.VIEWER)
        user = get_user_model().objects.get(pk=user.pk)
        self.assertEqual([g.name for g in user.groups.all()], ["Register viewers"])
        self.assertFalse(user.has_perm("registry.add_correspondence"))

    def test_admin_role_sets_staff_and_superuser(self):
        user = get_user_model().objects.create_user("promoted", password="pass12345!")
        apply_role(user, UserProfile.Role.ADMIN)
        user.refresh_from_db()
        self.assertTrue(user.is_staff and user.is_superuser)


class UserManagementAccessTests(TestCase):
    """User management is restricted to administrators."""

    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("boss", password="pass12345!")
        self.staff = get_user_model().objects.create_user("worker", password="pass12345!")

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("user_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_non_admin_gets_forbidden(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("user_list")).status_code, 403)

    def test_admin_sees_user_list(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("user_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "worker")

    def test_user_list_filters_by_role_and_search(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("user_list"), {"role": UserProfile.Role.ADMIN})
        self.assertEqual([u.username for u in response.context["page"]], ["boss"])
        response = self.client.get(reverse("user_list"), {"q": "work"})
        self.assertEqual([u.username for u in response.context["page"]], ["worker"])
        response = self.client.get(reverse("user_list"), {"status": "inactive"})
        self.assertEqual(list(response.context["page"]), [])


class UserManagementActionTests(TestCase):
    """Creating, editing, deactivating and resetting accounts."""

    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            "boss", email="boss@example.com", password="pass12345!")
        self.client.force_login(self.admin)

    def _create_payload(self, **overrides):
        payload = {
            "username": "newofficer",
            "first_name": "New",
            "last_name": "Officer",
            "email": "new.officer@example.com",
            "is_active": "on",
            "role": UserProfile.Role.OFFICER,
            "job_title": "Records clerk",
            "phone": "876-555-0100",
            "password1": "Temp-pass-98765",
            "password2": "Temp-pass-98765",
        }
        payload.update(overrides)
        return payload

    def test_admin_creates_account_that_must_change_password(self):
        response = self.client.post(reverse("user_create"), self._create_payload())
        self.assertRedirects(response, reverse("user_list"))
        user = get_user_model().objects.get(username="newofficer")
        self.assertTrue(user.check_password("Temp-pass-98765"))
        self.assertTrue(user.profile.must_change_password)
        self.assertEqual(user.profile.role, UserProfile.Role.OFFICER)
        self.assertTrue(user.has_perm("registry.add_correspondence"))
        self.assertTrue(AuditLog.objects.filter(event="User created").exists())

    def test_duplicate_username_is_rejected(self):
        self.client.post(reverse("user_create"), self._create_payload())
        response = self.client.post(
            reverse("user_create"),
            self._create_payload(email="other@example.com"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertEqual(get_user_model().objects.filter(username="newofficer").count(), 1)

    def test_weak_temporary_password_is_rejected(self):
        response = self.client.post(
            reverse("user_create"),
            self._create_payload(password1="12345", password2="12345"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username="newofficer").exists())

    def test_editing_a_user_changes_their_role(self):
        target = get_user_model().objects.create_user("target", email="t@example.com", password="pass12345!")
        response = self.client.post(reverse("user_edit", args=[target.pk]), {
            "username": "target",
            "first_name": "",
            "last_name": "",
            "email": "t@example.com",
            "is_active": "on",
            "role": UserProfile.Role.VIEWER,
            "job_title": "",
            "phone": "",
        })
        self.assertRedirects(response, reverse("user_list"))
        target.refresh_from_db()
        self.assertEqual(target.profile.role, UserProfile.Role.VIEWER)
        self.assertFalse(get_user_model().objects.get(pk=target.pk).has_perm("registry.add_correspondence"))

    def test_admin_cannot_demote_or_deactivate_self(self):
        response = self.client.post(reverse("user_edit", args=[self.admin.pk]), {
            "username": "boss",
            "first_name": "",
            "last_name": "",
            "email": "boss@example.com",
            "is_active": "",
            "role": UserProfile.Role.VIEWER,
            "job_title": "",
            "phone": "",
        })
        self.assertEqual(response.status_code, 200)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active and self.admin.is_superuser)

    def test_toggle_active_deactivates_and_reactivates(self):
        target = get_user_model().objects.create_user("target", password="pass12345!")
        self.client.post(reverse("user_toggle_active", args=[target.pk]))
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.client.post(reverse("user_toggle_active", args=[target.pk]))
        target.refresh_from_db()
        self.assertTrue(target.is_active)

    def test_cannot_deactivate_own_account(self):
        self.client.post(reverse("user_toggle_active", args=[self.admin.pk]))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_admin_resets_another_users_password(self):
        target = get_user_model().objects.create_user("target", password="pass12345!")
        response = self.client.post(reverse("user_reset_password", args=[target.pk]), {
            "new_password1": "Reset-pass-54321",
            "new_password2": "Reset-pass-54321",
            "require_change": "on",
        })
        self.assertRedirects(response, reverse("user_list"))
        target.refresh_from_db()
        self.assertTrue(target.check_password("Reset-pass-54321"))
        self.assertTrue(target.profile.must_change_password)
        self.assertTrue(AuditLog.objects.filter(event="Password reset by administrator").exists())

    def test_reset_rejects_mismatched_passwords(self):
        target = get_user_model().objects.create_user("target", password="pass12345!")
        response = self.client.post(reverse("user_reset_password", args=[target.pk]), {
            "new_password1": "Reset-pass-54321",
            "new_password2": "Different-pass-1",
        })
        self.assertEqual(response.status_code, 200)
        target.refresh_from_db()
        self.assertTrue(target.check_password("pass12345!"))


class ForcedPasswordChangeTests(TestCase):
    """Accounts flagged by an administrator must set a new password first."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("temp", password="Temp-pass-98765")
        UserProfile.objects.filter(user=self.user).update(must_change_password=True)
        self.client.force_login(self.user)

    def test_other_pages_redirect_to_password_change(self):
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, reverse("password_change"))

    def test_password_change_page_is_reachable(self):
        self.assertEqual(self.client.get(reverse("password_change")).status_code, 200)

    def test_changing_password_clears_the_flag_and_restores_access(self):
        response = self.client.post(reverse("password_change"), {
            "old_password": "Temp-pass-98765",
            "new_password1": "Chosen-pass-24680",
            "new_password2": "Chosen-pass-24680",
        })
        self.assertRedirects(response, reverse("dashboard"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.profile.must_change_password)
        self.assertIsNotNone(self.user.profile.password_changed_at)
        self.assertTrue(self.user.check_password("Chosen-pass-24680"))
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)


class PasswordResetFlowTests(TestCase):
    """Self-service password reset by email."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "forgetful", email="forgetful@example.com", password="pass12345!")

    def test_login_page_links_to_reset(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, reverse("password_reset"))

    def test_reset_email_is_sent_with_working_link(self):
        response = self.client.post(reverse("password_reset"), {"email": "forgetful@example.com"})
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        match = re.search(r"/accounts/password/reset/[^/]+/[^/\s]+/", mail.outbox[0].body)
        self.assertIsNotNone(match, "reset email should contain a confirm link")

        # The token link redirects to the set-password form, which holds the session token.
        confirm = self.client.get(match.group(0))
        response = self.client.post(
            confirm.url,
            {"new_password1": "Brand-new-pass-1", "new_password2": "Brand-new-pass-1"},
        )
        self.assertRedirects(response, reverse("password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Brand-new-pass-1"))

    def test_unknown_email_does_not_leak_account_existence(self):
        response = self.client.post(reverse("password_reset"), {"email": "nobody@example.com"})
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)


class ProfileSelfServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("selfserve", password="pass12345!")
        self.client.force_login(self.user)

    def test_user_updates_own_details(self):
        response = self.client.post(reverse("profile"), {
            "first_name": "Self", "last_name": "Serve",
            "email": "self@example.com", "job_title": "Analyst", "phone": "876-555-0111",
        })
        self.assertRedirects(response, reverse("profile"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Self")
        self.assertEqual(self.user.email, "self@example.com")
        self.assertEqual(self.user.profile.job_title, "Analyst")

    def test_duplicate_email_is_rejected(self):
        get_user_model().objects.create_user("other", email="taken@example.com", password="pass12345!")
        response = self.client.post(reverse("profile"), {
            "first_name": "", "last_name": "", "email": "taken@example.com",
            "job_title": "", "phone": "",
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.email, "taken@example.com")


class NavigationTests(TestCase):
    """The modernised shell only offers admin navigation to administrators."""

    def test_admin_sees_user_management_link(self):
        admin = get_user_model().objects.create_superuser("navboss", password="pass12345!")
        self.client.force_login(admin)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, reverse("user_list"))

    def test_regular_user_does_not_see_user_management_link(self):
        user = get_user_model().objects.create_user("navstaff", password="pass12345!")
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertNotContains(response, reverse("user_list"))
        self.assertContains(response, reverse("password_change"))


class RegistryPermissionTests(TestCase):
    """Roles must actually gate who can write to the register."""

    def setUp(self):
        self.viewer = get_user_model().objects.create_user("viewer", password="pass12345!")
        apply_role(self.viewer, UserProfile.Role.VIEWER)
        self.officer = get_user_model().objects.create_user("officer", password="pass12345!")

    def test_new_accounts_can_register_correspondence_by_default(self):
        self.client.force_login(self.officer)
        self.assertEqual(self.client.get(reverse("correspondence_create")).status_code, 200)

    def test_viewer_cannot_open_the_registration_form(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("correspondence_create")).status_code, 403)

    def test_viewer_cannot_post_a_new_record(self):
        self.client.force_login(self.viewer)
        response = self.client.post(
            reverse("correspondence_create"),
            {"direction": "in", "date_received": "2024-03-04", "sender": "A", "recipient": "B"},
        )
        self.assertEqual(response.status_code, 403)

    def test_viewer_still_reads_the_register(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("correspondence_list")).status_code, 200)

    def test_viewer_is_not_offered_write_actions(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("dashboard"))
        self.assertNotContains(response, reverse("correspondence_create"))


class RegisterImportTests(TestCase):
    """Covers parsing the historical log and the browser import workflow."""

    HEADER = [
        "DATE RECEIVED/SENT", "DATE OF DOCUMENT", "FROM", "TO", "FROM", "TO",
        "COPY TO or THRU'", "DESCRIPTION", "Lab#/ Ref #/Voucher#/Invoice#",
        "SUPERVISOR'S DIRECTIVES/COMMENTS OR GENERAL NOTATIONS/ACTION TAKEN",
        "RECEIVED/COLLECTED BY", "DATE RECEIVED/COLLECTED BY", "FURTHER COMMENTS",
    ]

    def setUp(self):
        self.officer = get_user_model().objects.create_user("importer", password="pass12345!")
        self.client.force_login(self.officer)

    def _workbook(self, rows, name="register.xlsx"):
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        for row in rows:
            sheet.append(row)
        buffer = BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        return SimpleUploadedFile(
            name, buffer.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def _sample_rows(self):
        return [
            ["", "", "", "", "", "", "", "CORRESPONDENCE LOG"],
            self.HEADER,
            ["", "", "", "", "", "", "", "YEAR 2021"],
            [datetime(2021, 4, 8), datetime(2021, 4, 8), "Mr. Stephenson", "Mr. Newland",
             "", "", "", "Letter Re: Confirmation", "LAB-1", "Filed", "Mr. Newland",
             datetime(2021, 4, 9), "none"],
            ["22-23/9/2025", "", "Ms. Green", "Mrs. Fraser", "Hostinger Ltd.", "Ms. Green",
             "", "Invoice query", "", "", "", "", ""],
        ]

    def test_parses_dates_divider_rows_and_duplicate_columns(self):
        parsed = parse_rows(rows_from_stream(self._workbook(self._sample_rows()), "register.xlsx"))
        self.assertEqual(len(parsed.records), 2)
        self.assertEqual(parsed.divider_rows, 1)

        first, second = parsed.records
        self.assertEqual(first.values["date_received_sent"], date(2021, 4, 8))
        self.assertEqual(first.values["date_received_collected"], date(2021, 4, 9))
        self.assertEqual(first.values["sender"], "Mr. Stephenson")
        # An unreadable date is reported but the source value is preserved.
        self.assertIsNone(second.values["date_received_sent"])
        self.assertTrue(second.warnings)
        self.assertEqual(second.original_row["DATE RECEIVED/SENT"], "22-23/9/2025")
        # The workbook repeats FROM/TO; the second pair is kept as source data.
        self.assertEqual(second.values["sender"], "Ms. Green")
        self.assertEqual(second.original_row["FROM (2)"], "Hostinger Ltd.")

    def test_import_creates_records_with_reference_ids(self):
        parsed = parse_rows(rows_from_stream(self._workbook(self._sample_rows()), "register.xlsx"))
        result = import_records(parsed, user=self.officer)
        self.assertEqual(result.created, 2)
        self.assertEqual(Correspondence.objects.count(), 2)
        for item in Correspondence.objects.all():
            self.assertRegex(item.reference_id, r"^COR-\d{4}-\d{6}$")

    def test_dry_run_changes_nothing(self):
        parsed = parse_rows(rows_from_stream(self._workbook(self._sample_rows()), "register.xlsx"))
        result = import_records(parsed, dry_run=True)
        self.assertEqual(result.created, 2)
        self.assertEqual(Correspondence.objects.count(), 0)

    def test_skip_existing_ignores_rows_already_registered(self):
        rows = self._sample_rows()
        parse_and_import = lambda: import_records(
            parse_rows(rows_from_stream(self._workbook(rows), "register.xlsx")),
            skip_existing=True,
        )
        parse_and_import()
        result = parse_and_import()
        self.assertEqual(result.created, 0)
        self.assertEqual(result.skipped, 2)
        self.assertEqual(Correspondence.objects.count(), 2)

    def test_preview_then_confirm_imports_the_uploaded_file(self):
        response = self.client.post(reverse("register_import"), {
            "file": self._workbook(self._sample_rows()),
            "direction": Correspondence.Direction.INCOMING,
            "skip_existing": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Letter Re: Confirmation")
        self.assertEqual(Correspondence.objects.count(), 0, "preview must not write")

        response = self.client.post(reverse("register_import_confirm"), {"action": "import"}, follow=True)
        self.assertEqual(Correspondence.objects.count(), 2)
        self.assertContains(response, "Imported 2 record(s)")
        self.assertEqual(
            set(Correspondence.objects.values_list("direction", flat=True)),
            {Correspondence.Direction.INCOMING},
        )
        self.assertTrue(AuditLog.objects.filter(event="Register imported").exists())

    def test_cancelling_the_preview_discards_the_upload(self):
        self.client.post(reverse("register_import"), {
            "file": self._workbook(self._sample_rows()),
            "direction": Correspondence.Direction.OTHER,
        })
        self.client.post(reverse("register_import_confirm"), {"action": "cancel"})
        self.assertEqual(Correspondence.objects.count(), 0)
        response = self.client.post(reverse("register_import_confirm"), {"action": "import"}, follow=True)
        self.assertContains(response, "no longer available")

    def test_file_without_register_headings_is_rejected(self):
        upload = self._workbook([["Some", "other", "sheet"], ["a", "b", "c"]])
        response = self.client.post(reverse("register_import"), {
            "file": upload, "direction": Correspondence.Direction.OTHER,
        })
        self.assertContains(response, "Could not identify the register header row")
        self.assertEqual(Correspondence.objects.count(), 0)

    def test_unsupported_file_type_is_rejected(self):
        upload = SimpleUploadedFile("notes.pdf", b"%PDF-1.4", content_type="application/pdf")
        response = self.client.post(reverse("register_import"), {
            "file": upload, "direction": Correspondence.Direction.OTHER,
        })
        self.assertContains(response, "Unsupported file type")

    def test_viewer_cannot_reach_the_import_screen(self):
        viewer = get_user_model().objects.create_user("importviewer", password="pass12345!")
        apply_role(viewer, UserProfile.Role.VIEWER)
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse("register_import")).status_code, 403)

    def test_management_command_imports_a_csv_file(self):
        path = Path(tempfile.mkdtemp()) / "register.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(self.HEADER)
            writer.writerow(["8-Apr-21", "8-Apr-21", "A", "B", "", "", "", "Letter", "", "", "", "", ""])
        out = StringIO()
        call_command("import_register", str(path), stdout=out)
        self.assertIn("Imported: 1", out.getvalue())
        item = Correspondence.objects.get()
        self.assertEqual(item.date_received_sent, date(2021, 4, 8))
