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
from registry.models import Action, ActionDirective, AuditLog, Branch, CollectionRecord, Correspondence, Notification, Position, UserProfile
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

    def test_registration_form_offers_origin_dropdown(self):
        self.client.force_login(self.officer)
        response = self.client.get(reverse("correspondence_create"))
        form = response.context["form"]
        self.assertEqual(
            set(value for value, _ in form.fields["origin"].choices if value),
            {"INTERNAL", "EXTERNAL", "OTHER"},
        )

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


class ActionEditTests(TestCase):
    """Actions carry a status and directive text, and can be edited."""

    def setUp(self):
        self.officer = get_user_model().objects.create_user("officer2", password="pass12345!")
        apply_role(self.officer, UserProfile.Role.OFFICER)
        self.viewer = get_user_model().objects.create_user("viewer2", password="pass12345!")
        apply_role(self.viewer, UserProfile.Role.VIEWER)
        self.item = Correspondence.objects.create(sender="A", recipient="B")
        self.action = Action.objects.create(
            correspondence=self.item,
            action_text="Follow up required",
        )

    def test_new_action_defaults_to_open_status(self):
        self.assertEqual(self.action.status, Action.Status.OPEN)

    def test_officer_can_edit_an_existing_action(self):
        self.client.force_login(self.officer)
        response = self.client.post(
            reverse("action_edit", args=[self.action.pk]),
            {
                "action_text": "Follow up completed",
                "action_date": self.action.action_date,
                "status": Action.Status.COMPLETED,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.action.refresh_from_db()
        self.assertEqual(self.action.status, Action.Status.COMPLETED)
        self.assertEqual(self.action.action_text, "Follow up completed")

    def test_viewer_cannot_edit_an_action(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("action_edit", args=[self.action.pk]))
        self.assertEqual(response.status_code, 403)

    def test_completed_status_shows_verified_icon_on_detail_page(self):
        self.action.status = Action.Status.COMPLETED
        self.action.save()
        self.client.force_login(self.officer)
        response = self.client.get(reverse("correspondence_detail", args=[self.item.pk]))
        self.assertContains(response, "bi-patch-check-fill")
        self.assertNotContains(response, "bi-x-circle")

    def test_closed_status_does_not_show_verified_icon(self):
        self.action.status = Action.Status.CLOSED
        self.action.save()
        self.client.force_login(self.officer)
        response = self.client.get(reverse("correspondence_detail", args=[self.item.pk]))
        self.assertContains(response, "bi-x-circle")
        self.assertNotContains(response, "bi-patch-check-fill")

    def test_detail_page_does_not_offer_action_from_to_fields(self):
        self.client.force_login(self.officer)
        response = self.client.get(reverse("correspondence_detail", args=[self.item.pk]))
        self.assertNotContains(response, 'name="from_name"')
        self.assertNotContains(response, 'name="assigned_to"')

    def test_pending_status_is_a_valid_action_choice(self):
        self.action.status = Action.Status.PENDING
        self.action.save()
        self.action.refresh_from_db()
        self.assertEqual(self.action.status, Action.Status.PENDING)


class CorrespondenceCurrentStatusTests(TestCase):
    """The register list shows a computed status instead of the reference column."""

    def setUp(self):
        self.officer = get_user_model().objects.create_user("officer4", password="pass12345!")
        apply_role(self.officer, UserProfile.Role.OFFICER)

    def test_correspondence_with_no_actions_is_not_addressed(self):
        item = Correspondence.objects.create(sender="A", recipient="B")
        self.assertEqual(item.current_status, "Not Addressed")

    def test_correspondence_status_reflects_latest_action(self):
        item = Correspondence.objects.create(sender="A", recipient="B")
        Action.objects.create(correspondence=item, action_text="Reviewed", status=Action.Status.PENDING)
        self.assertEqual(item.current_status, "Pending")

    def test_list_page_shows_status_column_not_reference(self):
        item = Correspondence.objects.create(sender="A", recipient="B", external_reference="VOUCHER-123")
        self.client.force_login(self.officer)
        response = self.client.get(reverse("correspondence_list"))
        self.assertContains(response, "Status")
        self.assertNotContains(response, "<th>Reference</th>")
        self.assertContains(response, "Not Addressed")
        self.assertNotContains(response, "VOUCHER-123")




class ActionFormOptionalFieldsTests(TestCase):
    """Action form fields are optional only when adding, not when editing."""

    def setUp(self):
        # Use same password syntax as other tests
        self.officer = get_user_model().objects.create_user("officer5", password="pass12345!")
        apply_role(self.officer, UserProfile.Role.OFFICER)
        self.item = Correspondence.objects.create(sender="A", recipient="B")

    def test_blank_action_submission_uses_defaults(self):
        """Submitting a blank action should create record with today's date and Open status."""
        self.client.force_login(self.officer)
        response = self.client.post(
            reverse("action_add", args=[self.item.pk]),
            {
                "action_text": "",
                "action_date": "",
                "status": "",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        action = Action.objects.get(correspondence=self.item)
        self.assertEqual(action.action_text, "")
        self.assertEqual(action.action_date, date.today())
        self.assertEqual(action.status, Action.Status.OPEN)

    def test_blank_collection_submission_uses_defaults(self):
        """Submitting a blank collection should create record with today's date."""
        from registry.models import CollectionRecord
        self.client.force_login(self.officer)
        response = self.client.post(
            reverse("collection_add", args=[self.item.pk]),
            {
                "collected_by": "",
                "collection_date": "",
                "notes": "",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        collection = CollectionRecord.objects.get(correspondence=self.item)
        self.assertEqual(collection.collected_by, "")
        self.assertEqual(collection.collection_date, date.today())
        self.assertEqual(collection.notes, "")

    def test_editing_action_requires_date_and_status(self):
        """Editing an action should require date and status to prevent accidental clearing."""
        self.client.force_login(self.officer)
        action = Action.objects.create(
            correspondence=self.item,
            action_text="Original text",
            action_date=date(2026, 11, 10),
            status=Action.Status.PENDING,
        )
        # Try to submit without date and status
        response = self.client.post(
            reverse("action_edit", args=[action.pk]),
            {
                "action_text": "Updated text",
                "action_date": "",
                "status": "",
            },
        )
        # Form should fail validation and return to form page
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "action_date", "This field is required.")
        self.assertFormError(response.context["form"], "status", "This field is required.")
        # Original values should be unchanged
        action.refresh_from_db()
        self.assertEqual(action.action_date, date(2026, 11, 10))
        self.assertEqual(action.status, Action.Status.PENDING)

    def test_editing_action_with_valid_data_succeeds(self):
        """Editing an action with valid data should update successfully."""
        self.client.force_login(self.officer)
        action = Action.objects.create(
            correspondence=self.item,
            action_text="Original text",
            action_date=date(2026, 11, 10),
            status=Action.Status.PENDING,
        )
        response = self.client.post(
            reverse("action_edit", args=[action.pk]),
            {
                "action_text": "Updated text",
                "action_date": str(date(2026, 11, 15)),
                "status": Action.Status.COMPLETED,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        action.refresh_from_db()
        self.assertEqual(action.action_text, "Updated text")
        self.assertEqual(action.action_date, date(2026, 11, 15))
        self.assertEqual(action.status, Action.Status.COMPLETED)


class LatestActionTextRenderingTests(TestCase):
    """The latest action text is correctly rendered in the correspondence list."""

    def setUp(self):
        self.officer = get_user_model().objects.create_user("officer6", password="pass12345!")
        apply_role(self.officer, UserProfile.Role.OFFICER)

    def test_list_shows_latest_action_text_with_multiple_actions(self):
        """List page should show the most recent action's text, not an older one."""
        item = Correspondence.objects.create(sender="A", recipient="B")
        # Create older action
        Action.objects.create(
            correspondence=item,
            action_text="First action text",
            action_date=date(2026, 11, 1),
        )
        # Create newer action
        Action.objects.create(
            correspondence=item,
            action_text="Latest action text",
            action_date=date(2026, 11, 15),
        )
        self.client.force_login(self.officer)
        response = self.client.get(reverse("correspondence_list"))
        self.assertContains(response, "Latest action text")
        self.assertNotContains(response, "First action text")

    def test_latest_action_text_property_returns_newest_action(self):
        """Model property should return the text of the newest action."""
        item = Correspondence.objects.create(sender="A", recipient="B")
        Action.objects.create(
            correspondence=item,
            action_text="Older action",
            action_date=date(2026, 11, 1),
        )
        Action.objects.create(
            correspondence=item,
            action_text="Newest action",
            action_date=date(2026, 11, 15),
        )
        self.assertEqual(item.latest_action_text, "Newest action")

    def test_latest_action_text_empty_when_no_actions(self):
        """Latest action text should be empty string when no actions exist."""
        item = Correspondence.objects.create(sender="A", recipient="B")
        self.assertEqual(item.latest_action_text, "")


class BranchPositionDropdownTests(TestCase):
    """Admin-managed Branch/Position lists populate the From/To dropdowns."""

    def setUp(self):
        self.officer = get_user_model().objects.create_user("officer3", password="pass12345!")
        apply_role(self.officer, UserProfile.Role.OFFICER)
        Branch.objects.create(name="Chemistry")
        Position.objects.create(name="Supervisor")

    def test_correspondence_form_offers_branch_and_position_choices(self):
        self.client.force_login(self.officer)
        response = self.client.get(reverse("correspondence_create"))
        form = response.context["form"]
        self.assertIn(("Chemistry", "Chemistry"), form.fields["sender_branch"].widget.choices)
        self.assertIn(("Supervisor", "Supervisor"), form.fields["sender_position"].widget.choices)

    def test_officer_can_submit_correspondence_with_dropdown_values(self):
        self.client.force_login(self.officer)
        response = self.client.post(
            reverse("correspondence_create"),
            {
                "direction": Correspondence.Direction.OTHER,
                "origin": Correspondence.Origin.OTHER,
                "sender": "A",
                "sender_branch": "Chemistry",
                "sender_position": "Supervisor",
                "recipient": "B",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        item = Correspondence.objects.get(sender="A")
        self.assertEqual(item.sender_branch, "Chemistry")
        self.assertEqual(item.sender_position, "Supervisor")

    def test_legacy_branch_position_values_appear_in_edit_form(self):
        """Legacy free-text values not in the lookup table should still appear as options."""
        self.client.force_login(self.officer)
        # Create a correspondence with legacy values not in the Branch/Position tables
        item = Correspondence.objects.create(
            direction=Correspondence.Direction.OTHER,
            sender="Old Sender",
            sender_branch="Old Branch",  # Not in Branch table
            sender_position="Old Position",  # Not in Position table
            recipient="Old Recipient",
            recipient_branch="Legacy Branch",  # Not in Branch table
            recipient_position="Legacy Position",  # Not in Position table
        )
        
        # Load the edit form for this correspondence
        response = self.client.get(reverse("correspondence_edit", args=[item.pk]))
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        
        # Verify that legacy values are included in the dropdown choices
        branch_choices = form.fields["sender_branch"].widget.choices
        position_choices = form.fields["sender_position"].widget.choices
        branch_choices_recipient = form.fields["recipient_branch"].widget.choices
        position_choices_recipient = form.fields["recipient_position"].widget.choices
        
        # Check that legacy values are in the choices
        branch_choice_values = [val for val, label in branch_choices]
        self.assertIn("Old Branch", branch_choice_values, "Legacy sender_branch should be in choices")
        self.assertIn("Legacy Branch", branch_choice_values, "Legacy recipient_branch should be in choices")
        
        position_choice_values = [val for val, label in position_choices]
        self.assertIn("Old Position", position_choice_values, "Legacy sender_position should be in choices")
        
        position_choice_values_recipient = [val for val, label in position_choices_recipient]
        self.assertIn("Legacy Position", position_choice_values_recipient, "Legacy recipient_position should be in choices")

    def test_submitting_edit_form_preserves_legacy_values(self):
        """When editing with legacy values, submitting should preserve them without clearing."""
        self.client.force_login(self.officer)
        # Create a correspondence with legacy values not in the Branch/Position tables
        item = Correspondence.objects.create(
            direction=Correspondence.Direction.OTHER,
            sender="Old Sender",
            sender_branch="Old Branch",
            sender_position="Old Position",
            recipient="Old Recipient",
            recipient_branch="Legacy Branch",
            recipient_position="Legacy Position",
        )
        
        # Submit the form without changing the branch/position fields
        # This simulates editing an unrelated field
        response = self.client.post(
            reverse("correspondence_edit", args=[item.pk]),
            {
                "direction": Correspondence.Direction.OTHER,
                "origin": Correspondence.Origin.OTHER,
                "sender": "Old Sender",
                "sender_branch": "Old Branch",
                "sender_position": "Old Position",
                "recipient": "Old Recipient - Updated",  # Only change recipient
                "recipient_branch": "Legacy Branch",
                "recipient_position": "Legacy Position",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        
        # Verify the values are preserved after save
        item.refresh_from_db()
        self.assertEqual(item.sender_branch, "Old Branch")
        self.assertEqual(item.sender_position, "Old Position")
        self.assertEqual(item.recipient_branch, "Legacy Branch")
        self.assertEqual(item.recipient_position, "Legacy Position")
        self.assertEqual(item.recipient, "Old Recipient - Updated")


class ActionDirectiveDropdownTests(TestCase):
    """Admin-managed ActionDirective list populates the action_text dropdown, with a free-text comment."""

    def setUp(self):
        self.officer = get_user_model().objects.create_user("officer7", password="pass12345!")
        apply_role(self.officer, UserProfile.Role.OFFICER)
        self.item = Correspondence.objects.create(sender="A", recipient="B")
        ActionDirective.objects.create(name="For comment")
        ActionDirective.objects.create(name="For approval")

    def test_action_form_offers_directive_choices(self):
        self.client.force_login(self.officer)
        response = self.client.get(reverse("correspondence_detail", args=[self.item.pk]))
        form = response.context["action_form"]
        self.assertIn(("For comment", "For comment"), form.fields["action_text"].widget.choices)
        self.assertIn(("For approval", "For approval"), form.fields["action_text"].widget.choices)

    def test_officer_can_add_action_with_dropdown_value_and_comment(self):
        self.client.force_login(self.officer)
        response = self.client.post(
            reverse("action_add", args=[self.item.pk]),
            {
                "action_text": "For approval",
                "comment": "Needs the director's sign-off first.",
                "action_date": date.today(),
                "status": Action.Status.OPEN,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        action = Action.objects.get(correspondence=self.item)
        self.assertEqual(action.action_text, "For approval")
        self.assertEqual(action.comment, "Needs the director's sign-off first.")

    def test_comment_is_optional(self):
        self.client.force_login(self.officer)
        response = self.client.post(
            reverse("action_add", args=[self.item.pk]),
            {
                "action_text": "For comment",
                "comment": "",
                "action_date": date.today(),
                "status": Action.Status.OPEN,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        action = Action.objects.get(correspondence=self.item)
        self.assertEqual(action.comment, "")

    def test_legacy_action_text_value_appears_in_edit_form(self):
        """Legacy free-text action_text not in the lookup table should still appear as an option."""
        action = Action.objects.create(
            correspondence=self.item,
            action_text="Legacy directive text",
        )
        self.client.force_login(self.officer)
        response = self.client.get(reverse("correspondence_detail", args=[self.item.pk]))
        self.assertContains(response, "Legacy directive text")

    def test_comment_displayed_on_detail_page(self):
        Action.objects.create(
            correspondence=self.item,
            action_text="For approval",
            comment="Escalated to the branch head.",
        )
        self.client.force_login(self.officer)
        response = self.client.get(reverse("correspondence_detail", args=[self.item.pk]))
        self.assertContains(response, "Escalated to the branch head.")


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

    def test_import_creates_collection_record_for_rows_with_a_collector(self):
        """"Received/collected by" rows should also populate Collection history."""
        from registry.models import CollectionRecord

        parsed = parse_rows(rows_from_stream(self._workbook(self._sample_rows()), "register.xlsx"))
        import_records(parsed, user=self.officer)

        # Only the first sample row has a "RECEIVED/COLLECTED BY" value.
        self.assertEqual(CollectionRecord.objects.count(), 1)
        collection = CollectionRecord.objects.get()
        self.assertEqual(collection.collected_by, "Mr. Newland")
        self.assertEqual(collection.collection_date, date(2021, 4, 9))
        self.assertEqual(collection.correspondence.sender, "Mr. Stephenson")

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


class BackfillCollectionRecordsCommandTests(TestCase):
    """The backfill command adds Collection history entries for older imports
    that predate automatic CollectionRecord creation during import."""

    def test_creates_entries_only_for_records_missing_one(self):
        from registry.models import CollectionRecord

        with_collector_no_record = Correspondence.objects.create(
            received_collected_by="Mr. Newland",
            date_received_collected=date(2021, 4, 9),
        )
        with_collector_has_record = Correspondence.objects.create(
            received_collected_by="Ms. Green",
            date_received_collected=date(2021, 5, 1),
        )
        CollectionRecord.objects.create(
            correspondence=with_collector_has_record, collected_by="Ms. Green",
            collection_date=date(2021, 5, 1),
        )
        Correspondence.objects.create(received_collected_by="")

        out = StringIO()
        call_command("backfill_collection_records", stdout=out)

        self.assertIn("Created 1 collection record", out.getvalue())
        self.assertEqual(CollectionRecord.objects.count(), 2)
        new_record = CollectionRecord.objects.get(correspondence=with_collector_no_record)
        self.assertEqual(new_record.collected_by, "Mr. Newland")
        self.assertEqual(new_record.collection_date, date(2021, 4, 9))

    def test_dry_run_does_not_save_anything(self):
        from registry.models import CollectionRecord

        Correspondence.objects.create(received_collected_by="Mr. Newland")
        out = StringIO()
        call_command("backfill_collection_records", "--dry-run", stdout=out)
        self.assertIn("Would create 1 collection record", out.getvalue())
        self.assertEqual(CollectionRecord.objects.count(), 0)


class CorrespondenceDeleteTests(TestCase):
    """Only admins can delete correspondence; deletion is audited and notified."""

    def setUp(self):
        self.admin = get_user_model().objects.create_user("deladmin", password="pass12345!")
        apply_role(self.admin, UserProfile.Role.ADMIN)
        self.officer = get_user_model().objects.create_user("delofficer", password="pass12345!")
        apply_role(self.officer, UserProfile.Role.OFFICER)
        self.item = Correspondence.objects.create(sender="A", recipient="B", description="Needs removal")

    def test_officer_cannot_delete(self):
        self.client.force_login(self.officer)
        response = self.client.post(reverse("correspondence_delete", args=[self.item.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Correspondence.objects.filter(pk=self.item.pk).exists())

    def test_officer_is_not_offered_delete_button(self):
        self.client.force_login(self.officer)
        response = self.client.get(reverse("correspondence_detail", args=[self.item.pk]))
        self.assertNotContains(response, reverse("correspondence_delete", args=[self.item.pk]))

    def test_admin_sees_delete_button(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("correspondence_detail", args=[self.item.pk]))
        self.assertContains(response, reverse("correspondence_delete", args=[self.item.pk]))

    def test_admin_can_delete_and_it_is_audited(self):
        reference_id = self.item.reference_id
        self.client.force_login(self.admin)
        response = self.client.post(reverse("correspondence_delete", args=[self.item.pk]))
        self.assertRedirects(response, reverse("correspondence_list"))
        self.assertFalse(Correspondence.objects.filter(pk=self.item.pk).exists())
        log = AuditLog.objects.get(event="Deleted")
        self.assertEqual(log.user, self.admin)
        self.assertIn(reference_id, log.details)

    def test_deleting_notifies_other_active_users_but_not_the_actor(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("correspondence_delete", args=[self.item.pk]))
        self.assertTrue(Notification.objects.filter(recipient=self.officer).exists())
        self.assertFalse(Notification.objects.filter(recipient=self.admin).exists())

    def test_get_request_shows_confirmation_page_without_deleting(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("correspondence_delete", args=[self.item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Correspondence.objects.filter(pk=self.item.pk).exists())


class NotificationTests(TestCase):
    """Users are notified in-app when correspondence changes."""

    def setUp(self):
        self.actor = get_user_model().objects.create_user("notactor", password="pass12345!")
        apply_role(self.actor, UserProfile.Role.OFFICER)
        self.other = get_user_model().objects.create_user("notother", password="pass12345!")
        apply_role(self.other, UserProfile.Role.VIEWER)

    def test_creating_a_record_notifies_other_users(self):
        self.client.force_login(self.actor)
        self.client.post(reverse("correspondence_create"), {
            "direction": "IN", "origin": "OTHER", "sender": "A", "recipient": "B",
        })
        item = Correspondence.objects.get()
        notification = Notification.objects.get(recipient=self.other)
        self.assertIn(item.reference_id, notification.message)
        self.assertEqual(notification.correspondence_id, item.pk)
        self.assertFalse(Notification.objects.filter(recipient=self.actor).exists())

    def test_editing_a_record_notifies_other_users(self):
        item = Correspondence.objects.create(sender="A", recipient="B")
        self.client.force_login(self.actor)
        self.client.post(reverse("correspondence_edit", args=[item.pk]), {
            "direction": "IN", "origin": "OTHER", "sender": "A", "recipient": "C",
        })
        self.assertTrue(Notification.objects.filter(recipient=self.other, correspondence=item).exists())

    def test_notification_list_only_shows_own_notifications(self):
        Notification.objects.create(recipient=self.other, message="For other")
        Notification.objects.create(recipient=self.actor, message="For actor")
        self.client.force_login(self.actor)
        response = self.client.get(reverse("notification_list"))
        self.assertContains(response, "For actor")
        self.assertNotContains(response, "For other")

    def test_mark_all_read_clears_unread_count(self):
        Notification.objects.create(recipient=self.actor, message="One")
        Notification.objects.create(recipient=self.actor, message="Two")
        self.client.force_login(self.actor)
        self.client.post(reverse("notification_mark_all_read"))
        self.assertEqual(Notification.objects.filter(recipient=self.actor, is_read=False).count(), 0)

class CollectionHistoryFallbackTests(TestCase):
    """Covers the collection_history property added for the register fallback."""

    def test_falls_back_to_register_fields_when_no_collection_record(self):
        item = Correspondence.objects.create(
            received_collected_by="DEWAN",
            date_received_collected=date(2026, 11, 19),
        )
        history = item.collection_history
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["collected_by"], "DEWAN")
        self.assertEqual(history[0]["collection_date"], date(2026, 11, 19))
        self.assertTrue(history[0]["from_register"])

    def test_prefers_explicit_collection_records_over_register_fields(self):
        item = Correspondence.objects.create(
            received_collected_by="DEWAN",
            date_received_collected=date(2026, 11, 19),
        )
        CollectionRecord.objects.create(correspondence=item, collected_by="JANE", collection_date=date(2026, 11, 20))
        history = item.collection_history
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].collected_by, "JANE")

    def test_empty_when_nothing_recorded(self):
        item = Correspondence.objects.create()
        self.assertEqual(item.collection_history, [])
