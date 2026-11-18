from django.urls import path
from . import views

urlpatterns = [
    path("", views.correspondence_list, name="correspondence_list"),
    path("new/", views.correspondence_create, name="correspondence_create"),
    path("export.csv", views.export_csv, name="export_csv"),
    path("import/", views.register_import, name="register_import"),
    path("import/confirm/", views.register_import_confirm, name="register_import_confirm"),
    path("<int:pk>/", views.correspondence_detail, name="correspondence_detail"),
    path("<int:pk>/edit/", views.correspondence_edit, name="correspondence_edit"),
    path("<int:pk>/delete/", views.correspondence_delete, name="correspondence_delete"),
    path("<int:pk>/attachments/add/", views.attachment_upload, name="attachment_upload"),
    path("<int:pk>/actions/add/", views.action_add, name="action_add"),
    path("actions/<int:pk>/edit/", views.action_edit, name="action_edit"),
    path("<int:pk>/collections/add/", views.collection_add, name="collection_add"),
    path("attachments/<int:attachment_id>/download/", views.attachment_download, name="attachment_download"),
    path("notifications/", views.notification_list, name="notification_list"),
    path("notifications/<int:pk>/read/", views.notification_mark_read, name="notification_mark_read"),
    path("notifications/mark-all-read/", views.notification_mark_all_read, name="notification_mark_all_read"),
]
