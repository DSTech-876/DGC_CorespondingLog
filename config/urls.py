from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from registry import views, views_accounts
from registry.forms_accounts import (
    StyledAuthenticationForm,
    StyledPasswordResetForm,
    StyledSetPasswordForm,
)

urlpatterns = [
    path("admin/", admin.site.urls),

    # Authentication
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html",
            authentication_form=StyledAuthenticationForm,
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),

    # Password management
    path("accounts/password/change/", views_accounts.password_change, name="password_change"),
    path(
        "accounts/password/reset/",
        auth_views.PasswordResetView.as_view(
            template_name="registration/password_reset_form.html",
            email_template_name="registration/password_reset_email.txt",
            subject_template_name="registration/password_reset_subject.txt",
            form_class=StyledPasswordResetForm,
        ),
        name="password_reset",
    ),
    path(
        "accounts/password/reset/sent/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="registration/password_reset_done.html"
        ),
        name="password_reset_done",
    ),
    path(
        "accounts/password/reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="registration/password_reset_confirm.html",
            form_class=StyledSetPasswordForm,
        ),
        name="password_reset_confirm",
    ),
    path(
        "accounts/password/reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="registration/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),

    # Account self-service and administrator user management
    path("accounts/profile/", views_accounts.profile, name="profile"),
    path("users/", include("registry.urls_users")),

    path("", views.dashboard, name="dashboard"),
    path("correspondence/", include("registry.urls")),
]
