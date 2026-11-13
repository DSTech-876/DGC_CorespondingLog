"""Administrator user management and account self-service views."""

from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordResetConfirmView as DjangoPasswordResetConfirmView
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms_accounts import (
    AdminSetPasswordForm,
    PasswordResetConfirmForm,
    ProfileForm,
    StyledPasswordChangeForm,
    UserCreateForm,
    UserEditForm,
)
from .models import AuditLog, UserProfile
from .roles import ROLE_DESCRIPTIONS, is_registry_admin

User = get_user_model()
Role = UserProfile.Role


def admin_required(view):
    """Restrict a view to accounts that may manage users.

    Authenticated non-administrators get 403 rather than being bounced back to
    the sign-in page, which would otherwise loop.
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_registry_admin(request.user):
            raise PermissionDenied("User management is restricted to administrators.")
        return view(request, *args, **kwargs)

    return wrapper


def _log(request, event, details=""):
    AuditLog.objects.create(user=request.user, correspondence=None, event=event, details=details)


def _profile_of(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


@login_required
@admin_required
def user_list(request):
    qs = User.objects.select_related("profile").order_by("username")
    q = request.GET.get("q", "").strip()
    role = request.GET.get("role", "")
    status = request.GET.get("status", "")
    if q:
        qs = qs.filter(
            Q(username__icontains=q) | Q(first_name__icontains=q)
            | Q(last_name__icontains=q) | Q(email__icontains=q)
        )
    if role in Role.values:
        qs = qs.filter(profile__role=role)
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "inactive":
        qs = qs.filter(is_active=False)

    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    context = {
        "page": page,
        "q": q,
        "role": role,
        "status": status,
        "roles": Role.choices,
        "total_users": User.objects.count(),
        "active_users": User.objects.filter(is_active=True).count(),
        "admin_users": User.objects.filter(profile__role=Role.ADMIN).count(),
        "pending_password_changes": UserProfile.objects.filter(must_change_password=True).count(),
    }
    return render(request, "registry/users/list.html", context)


@login_required
@admin_required
def user_create(request):
    form = UserCreateForm(request.POST or None, editing_user=request.user)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        _log(request, "User created", f"{user.username} ({form.cleaned_data['role']})")
        messages.success(
            request,
            f'Account "{user.username}" created. Share the temporary password securely — '
            "it must be changed at first sign in.",
        )
        return redirect("user_list")
    return render(request, "registry/users/form.html", {
        "form": form,
        "title": "Add user account",
        "subtitle": "Create a staff account and assign its role.",
        "submit_label": "Create account",
        "role_descriptions": ROLE_DESCRIPTIONS,
    })


@login_required
@admin_required
def user_edit(request, pk):
    target = get_object_or_404(User.objects.select_related("profile"), pk=pk)
    form = UserEditForm(request.POST or None, instance=target, editing_user=request.user)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        _log(request, "User updated", f"{user.username} ({form.cleaned_data['role']})")
        messages.success(request, f'Account "{user.username}" updated.')
        return redirect("user_list")
    return render(request, "registry/users/form.html", {
        "form": form,
        "target": target,
        "title": f"Edit {target.username}",
        "subtitle": "Update details, role and access for this account.",
        "submit_label": "Save changes",
        "role_descriptions": ROLE_DESCRIPTIONS,
    })


@login_required
@admin_required
@require_POST
def user_toggle_active(request, pk):
    target = get_object_or_404(User, pk=pk)
    if target.pk == request.user.pk:
        messages.error(request, "You cannot deactivate your own account.")
        return redirect("user_list")
    target.is_active = not target.is_active
    target.save(update_fields=["is_active"])
    state = "activated" if target.is_active else "deactivated"
    _log(request, f"User {state}", target.username)
    messages.success(request, f'Account "{target.username}" {state}.')
    return redirect("user_list")


@login_required
@admin_required
def user_reset_password(request, pk):
    target = get_object_or_404(User.objects.select_related("profile"), pk=pk)
    form = AdminSetPasswordForm(target, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        if target.pk == request.user.pk:
            update_session_auth_hash(request, target)
        _log(request, "Password reset by administrator", target.username)
        messages.success(
            request,
            f'Password reset for "{target.username}". Share the new password securely.',
        )
        return redirect("user_list")
    return render(request, "registry/users/reset_password.html", {"form": form, "target": target})


@login_required
def profile(request):
    """Self-service details page for the signed-in user."""
    instance = _profile_of(request.user)
    form = ProfileForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Your details were updated.")
        return redirect("profile")
    return render(request, "registry/users/profile.html", {"form": form, "profile": instance})


@login_required
def password_change(request):
    """Change your own password, clearing any administrator-forced change."""
    forced = _profile_of(request.user).must_change_password
    form = StyledPasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        UserProfile.objects.filter(user=user).update(
            must_change_password=False, password_changed_at=timezone.now()
        )
        _log(request, "Password changed", user.username)
        messages.success(request, "Your password was changed.")
        return redirect("dashboard")
    template = "registration/password_change_forced.html" if forced else "registration/password_change_form.html"
    return render(request, template, {"form": form, "forced": forced})


class PasswordResetConfirmView(DjangoPasswordResetConfirmView):
    """Custom password reset confirm view that updates the user profile."""

    form_class = PasswordResetConfirmForm
    template_name = "registration/password_reset_confirm.html"

    def form_valid(self, form):
        """Save password and clear the forced-change flag."""
        user = form.save()
        # Update profile to clear forced password change and record the change time
        UserProfile.objects.filter(user=user).update(
            must_change_password=False,
            password_changed_at=timezone.now()
        )
        return super().form_valid(form)

