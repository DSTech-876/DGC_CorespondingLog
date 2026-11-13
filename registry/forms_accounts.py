"""Forms for account self-service and administrator user management."""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
)
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password

from .forms import BootstrapFormMixin
from .models import UserProfile
from .roles import ROLE_GROUPS, apply_role

User = get_user_model()
Role = UserProfile.Role

ROLE_CHOICES = [(role.value, role.label) for role in Role]


class StyledAuthenticationForm(BootstrapFormMixin, AuthenticationForm):
    """Sign-in form with modern widget styling and autofocus."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update(
            {"placeholder": "Username", "autocomplete": "username", "autofocus": True}
        )
        self.fields["password"].widget.attrs.update(
            {"placeholder": "Password", "autocomplete": "current-password"}
        )


class StyledPasswordChangeForm(BootstrapFormMixin, PasswordChangeForm):
    pass


class StyledPasswordResetForm(BootstrapFormMixin, PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].widget.attrs.update(
            {"placeholder": "you@example.com", "autocomplete": "email", "autofocus": True}
        )


class StyledSetPasswordForm(BootstrapFormMixin, SetPasswordForm):
    pass


class ProfileForm(BootstrapFormMixin, forms.ModelForm):
    """Lets a signed-in user maintain their own name and contact details."""

    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=False, help_text="Used to send password reset links.")

    class Meta:
        model = UserProfile
        fields = ["job_title", "phone"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.instance.user
        self.fields["first_name"].initial = user.first_name
        self.fields["last_name"].initial = user.last_name
        self.fields["email"].initial = user.email
        self.order_fields(["first_name", "last_name", "email", "job_title", "phone"])

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip()
        if email and User.objects.filter(email__iexact=email).exclude(pk=self.instance.user_id).exists():
            raise forms.ValidationError("Another account already uses this email address.")
        return email

    def save(self, commit=True):
        profile = super().save(commit=False)
        user = profile.user
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        user.email = self.cleaned_data["email"]
        if commit:
            user.save(update_fields=["first_name", "last_name", "email"])
            profile.save()
        return profile


class BaseUserAdminForm(BootstrapFormMixin, forms.ModelForm):
    """Shared fields and role handling for the administrator user screens."""

    role = forms.ChoiceField(choices=ROLE_CHOICES, initial=Role.OFFICER.value)
    extra_groups = forms.ModelMultipleChoiceField(
        label="Additional groups",
        queryset=Group.objects.none(),
        required=False,
        help_text="Optional permission groups applied on top of the role.",
    )
    job_title = forms.CharField(max_length=120, required=False)
    phone = forms.CharField(max_length=40, required=False)

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active"]

    def __init__(self, *args, **kwargs):
        self.editing_user = kwargs.pop("editing_user", None)
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True
        self.fields["is_active"].help_text = (
            "Inactive accounts cannot sign in but are retained for audit history."
        )
        role_group_names = [name for name, _ in ROLE_GROUPS.values()]
        self.fields["extra_groups"].queryset = Group.objects.exclude(name__in=role_group_names)
        self.fields["role"].help_text = "Determines permissions — see the role guide alongside this form."
        self.order_fields([
            "username", "first_name", "last_name", "email",
            "role", "job_title", "phone", "extra_groups", "is_active",
        ])

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("An account with that username already exists.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("An account with that email address already exists.")
        return email

    def _save_profile_and_role(self, user):
        apply_role(user, self.cleaned_data["role"], self.cleaned_data.get("extra_groups") or [])
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.job_title = self.cleaned_data.get("job_title", "")
        profile.phone = self.cleaned_data.get("phone", "")
        profile.role = self.cleaned_data["role"]
        profile.save(update_fields=["job_title", "phone", "role"])
        return user


class UserCreateForm(BaseUserAdminForm):
    """Creates an account with a temporary password the user must replace."""

    password1 = forms.CharField(
        label="Temporary password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Share this securely. The user must change it at first sign in.",
    )
    password2 = forms.CharField(
        label="Confirm temporary password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean(self):
        cleaned = super().clean()
        password1, password2 = cleaned.get("password1"), cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The two password fields do not match.")
        elif password1:
            candidate = User(
                username=cleaned.get("username", ""),
                email=cleaned.get("email", ""),
                first_name=cleaned.get("first_name", ""),
                last_name=cleaned.get("last_name", ""),
            )
            try:
                validate_password(password1, candidate)
            except forms.ValidationError as error:
                self.add_error("password1", error)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        user.save()
        self._save_profile_and_role(user)
        UserProfile.objects.filter(user=user).update(must_change_password=True)
        return user


class UserEditForm(BaseUserAdminForm):
    """Updates details, role and activation state for an existing account."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.instance
        if user.pk:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            self.fields["role"].initial = profile.role
            self.fields["extra_groups"].initial = user.groups.exclude(
                name__in=[name for name, _ in ROLE_GROUPS.values()]
            )
            self.fields["job_title"].initial = profile.job_title
            self.fields["phone"].initial = profile.phone

    def clean(self):
        cleaned = super().clean()
        if self.editing_user and self.editing_user.pk == self.instance.pk:
            if cleaned.get("role") and cleaned["role"] != Role.ADMIN.value:
                self.add_error("role", "You cannot remove your own administrator role.")
            if not cleaned.get("is_active"):
                self.add_error("is_active", "You cannot deactivate your own account.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        if commit:
            user.save()
            self._save_profile_and_role(user)
        return user


class AdminSetPasswordForm(BootstrapFormMixin, forms.Form):
    """Administrator-initiated password reset for another account."""

    new_password1 = forms.CharField(
        label="New password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "autofocus": True}),
    )
    new_password2 = forms.CharField(
        label="Confirm new password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    require_change = forms.BooleanField(
        label="Require the user to choose a new password at next sign in",
        required=False,
        initial=True,
    )

    def __init__(self, target_user, *args, **kwargs):
        self.target_user = target_user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        password1, password2 = cleaned.get("new_password1"), cleaned.get("new_password2")
        if password1 and password2 and password1 != password2:
            self.add_error("new_password2", "The two password fields do not match.")
        elif password1:
            try:
                validate_password(password1, self.target_user)
            except forms.ValidationError as error:
                self.add_error("new_password1", error)
        return cleaned

    def save(self):
        from django.utils import timezone
        self.target_user.set_password(self.cleaned_data["new_password1"])
        self.target_user.save(update_fields=["password"])
        profile, _ = UserProfile.objects.get_or_create(user=self.target_user)
        profile.must_change_password = bool(self.cleaned_data.get("require_change"))
        profile.password_changed_at = timezone.now()
        profile.save(update_fields=["must_change_password", "password_changed_at"])
        return self.target_user


class PasswordResetConfirmForm(BootstrapFormMixin, SetPasswordForm):
    """Password reset confirmation form that clears the forced-change flag."""
    pass

