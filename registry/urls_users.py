"""Administrator user management routes (mounted at /users/)."""

from django.urls import path

from . import views_accounts

urlpatterns = [
    path("", views_accounts.user_list, name="user_list"),
    path("new/", views_accounts.user_create, name="user_create"),
    path("<int:pk>/edit/", views_accounts.user_edit, name="user_edit"),
    path("<int:pk>/password/", views_accounts.user_reset_password, name="user_reset_password"),
    path("<int:pk>/toggle-active/", views_accounts.user_toggle_active, name="user_toggle_active"),
]
