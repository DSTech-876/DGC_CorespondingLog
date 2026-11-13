"""Project middleware for the correspondence register."""

from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse

#: URL names a user may still reach while a password change is outstanding.
EXEMPT_URL_NAMES = {
    "password_change",
    "logout",
    "login",
    "password_reset",
    "password_reset_done",
    "password_reset_confirm",
    "password_reset_complete",
}


class ForcePasswordChangeMiddleware:
    """Redirects users flagged by an administrator to the change-password page.

    The flag is set when an administrator creates an account or resets a
    password, so temporary passwords cannot keep being used.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and self._needs_change(user):
            try:
                url_name = resolve(request.path_info).url_name
            except Resolver404:
                url_name = None
            if url_name not in EXEMPT_URL_NAMES and not request.path_info.startswith("/static/"):
                return redirect(reverse("password_change"))
        return self.get_response(request)

    @staticmethod
    def _needs_change(user):
        profile = getattr(user, "profile", None)
        return bool(profile and profile.must_change_password)
