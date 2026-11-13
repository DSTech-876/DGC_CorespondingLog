import os
import secrets
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = (
        "Create or update the initial administrator account without an interactive "
        "prompt. Values default to DJANGO_SUPERUSER_USERNAME / DJANGO_SUPERUSER_EMAIL "
        "/ DJANGO_SUPERUSER_PASSWORD. Safe to re-run."
    )

    def add_arguments(self, parser):
        parser.add_argument("--username", default=None)
        parser.add_argument("--email", default=None)
        parser.add_argument("--password", default=None)
        parser.add_argument(
            "--reset-password",
            action="store_true",
            help="Also reset the password when the account already exists.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"] or os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin")
        email = options["email"] or os.environ.get("DJANGO_SUPERUSER_EMAIL", "")
        password = options["password"] or os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")
        if not username:
            raise CommandError("A username is required (--username or DJANGO_SUPERUSER_USERNAME).")

        generated = False
        if not password:
            password = secrets.token_urlsafe(16)
            generated = True

        user = User.objects.filter(**{User.USERNAME_FIELD: username}).first()
        if user is None:
            User.objects.create_superuser(username, email=email, password=password)
            self.stdout.write(self.style.SUCCESS(f"Created superuser '{username}'."))
            if generated:
                self._write_generated_password(password)
            return

        changed = []
        if not (user.is_staff and user.is_superuser and user.is_active):
            user.is_staff = user.is_superuser = user.is_active = True
            changed.append("privileges")
        if email and user.email != email:
            user.email = email
            changed.append("email")
        if options["reset_password"]:
            user.set_password(password)
            changed.append("password")
        user.save()

        summary = ", ".join(changed) if changed else "no changes needed"
        self.stdout.write(self.style.SUCCESS(f"Superuser '{username}' already exists ({summary})."))
        if options["reset_password"] and generated:
            self._write_generated_password(password)

    def _write_generated_password(self, password):
        self.stdout.write(self.style.WARNING(f"Generated password: {password}"))
        self.stdout.write(self.style.WARNING("Store it securely and change it after first sign in."))
