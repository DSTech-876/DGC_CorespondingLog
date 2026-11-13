from django.conf import settings
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


class UserProfile(models.Model):
    """Per-account settings the register needs on top of ``auth.User``."""

    class Role(models.TextChoices):
        VIEWER = "viewer", "Viewer"
        OFFICER = "officer", "Records officer"
        ADMIN = "admin", "Administrator"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.OFFICER)
    job_title = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    must_change_password = models.BooleanField(
        default=False,
        help_text="Forces the user to choose a new password at next sign in.",
    )
    password_changed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile for {self.user}"


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_user_profile(sender, instance, created, **kwargs):
    """Give every new account a profile and its default role permissions."""
    if not created:
        return
    from .roles import get_role_group  # imported late: roles imports this module

    role = UserProfile.Role.ADMIN if instance.is_superuser else UserProfile.Role.OFFICER
    UserProfile.objects.get_or_create(user=instance, defaults={"role": role})
    instance.groups.add(get_role_group(role))


class Correspondence(models.Model):
    class Direction(models.TextChoices):
        INCOMING = "IN", "Incoming"
        OUTGOING = "OUT", "Outgoing"
        OTHER = "OTHER", "Other / Not specified"

    reference_id = models.CharField(max_length=30, unique=True, blank=True)
    direction = models.CharField(max_length=5, choices=Direction.choices, default=Direction.OTHER)
    date_received_sent = models.DateField(null=True, blank=True, db_index=True)
    date_of_document = models.DateField(null=True, blank=True)
    sender = models.TextField(blank=True)
    recipient = models.TextField(blank=True)
    copy_to_thru = models.TextField(blank=True)
    description = models.TextField(blank=True, db_index=True)
    external_reference = models.TextField(blank=True, help_text="Lab #, Ref #, Voucher #, Invoice #, etc.")
    supervisor_directives = models.TextField(blank=True)
    received_collected_by = models.TextField(blank=True)
    date_received_collected = models.DateField(null=True, blank=True)
    further_comments = models.TextField(blank=True)
    original_row = models.JSONField(default=dict, blank=True, help_text="Preserves source spreadsheet values during migration.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_correspondence")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_received_sent", "-id"]
        permissions = [
            ("view_sensitive_correspondence", "Can view sensitive correspondence"),
            ("export_correspondence", "Can export correspondence"),
        ]

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new and not self.reference_id:
            self.reference_id = f"COR-{self.created_at:%Y}-{self.pk:06d}"
            super().save(update_fields=["reference_id"])

    def __str__(self):
        return f"{self.reference_id or 'Unnumbered'} — {self.description[:70]}"

class Attachment(models.Model):
    correspondence = models.ForeignKey(Correspondence, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="correspondence/%Y/%m/")
    original_filename = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    notes = models.CharField(max_length=500, blank=True)

    def save(self, *args, **kwargs):
        if self.file and not self.original_filename:
            self.original_filename = self.file.name.rsplit("/", 1)[-1]
        super().save(*args, **kwargs)

class Action(models.Model):
    correspondence = models.ForeignKey(Correspondence, on_delete=models.CASCADE, related_name="actions")
    action_text = models.TextField()
    action_date = models.DateField(default=timezone.localdate)
    assigned_to = models.CharField(max_length=255, blank=True)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-action_date", "-id"]

class CollectionRecord(models.Model):
    correspondence = models.ForeignKey(Correspondence, on_delete=models.CASCADE, related_name="collection_records")
    collected_by = models.CharField(max_length=255)
    collection_date = models.DateField(default=timezone.localdate)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

class AuditLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    correspondence = models.ForeignKey(Correspondence, null=True, on_delete=models.SET_NULL)
    event = models.CharField(max_length=100)
    details = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at"]
