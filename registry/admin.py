from django.contrib import admin
from .models import Correspondence, Attachment, Action, CollectionRecord, AuditLog

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
