from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import Correspondence, Attachment, Action, CollectionRecord, AuditLog, UserProfile
from .roles import apply_role

class AttachmentInline(admin.TabularInline):
    model = Attachment
    extra = 0
class ActionInline(admin.TabularInline):
    model = Action
    extra = 0
class CollectionInline(admin.TabularInline):
    model = CollectionRecord
    extra = 0

@admin.register(Correspondence)
class CorrespondenceAdmin(admin.ModelAdmin):
    list_display = ("reference_id", "direction", "date_received_sent", "sender", "recipient", "description")
    list_filter = ("direction", "date_received_sent")
    search_fields = ("reference_id", "sender", "recipient", "description", "external_reference")
    inlines = [AttachmentInline, ActionInline, CollectionInline]
    readonly_fields = ("reference_id", "created_at", "updated_at")

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "event", "correspondence")
    readonly_fields = ("user", "correspondence", "event", "details", "created_at")


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    extra = 0
    fields = ("role", "job_title", "phone", "must_change_password", "password_changed_at")
    readonly_fields = ("password_changed_at",)

    def save_model(self, request, obj, form, change):
        """Save profile and re-apply role to ensure permissions are synced."""
        super().save_model(request, obj, form, change)
        # Re-apply the role to ensure permissions are synchronized with ROLE_GROUPS
        if obj.user:
            apply_role(obj.user, obj.role)


class UserAdmin(BaseUserAdmin):
    """Shows registry role and profile fields alongside the standard user form."""

    inlines = [UserProfileInline]
    list_display = ("username", "email", "first_name", "last_name", "registry_role", "is_active", "is_superuser")
    list_select_related = ("profile",)

    @admin.display(description="Registry role", ordering="profile__role")
    def registry_role(self, obj):
        profile = getattr(obj, "profile", None)
        return profile.get_role_display() if profile else "—"


admin.site.unregister(User)
admin.site.register(User, UserAdmin)
