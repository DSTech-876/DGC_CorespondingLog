"""Role definitions mapping registry roles onto Django auth groups.

Roles keep permission handling declarative: changing a user's role in the admin
UI re-synchronises their group membership and staff/superuser flags.
"""

from django.contrib.auth.models import Group, Permission

from .models import UserProfile

Role = UserProfile.Role

#: Permission codenames (``app_label.codename``) granted by each role group.
ROLE_GROUPS = {
    Role.VIEWER: (
        "Register viewers",
        ["registry.view_correspondence", "registry.view_attachment",
         "registry.view_action", "registry.view_collectionrecord"],
    ),
    Role.OFFICER: (
        "Records officers",
        ["registry.view_correspondence", "registry.add_correspondence", "registry.change_correspondence",
         "registry.view_attachment", "registry.add_attachment",
         "registry.view_action", "registry.add_action",
         "registry.view_collectionrecord", "registry.add_collectionrecord",
         "registry.export_correspondence"],
    ),
    Role.ADMIN: (
        "Registry administrators",
        ["registry.export_correspondence", "registry.view_sensitive_correspondence"],
    ),
}

ROLE_DESCRIPTIONS = {
    Role.VIEWER: "Read-only access to the correspondence register.",
    Role.OFFICER: "Register and update correspondence, upload attachments and export CSV.",
    Role.ADMIN: "Full access, including user management and the Django admin site.",
}


def get_role_group(role):
    """Return (creating if needed) the auth group backing ``role``."""
    name, codenames = ROLE_GROUPS[role]
    group, created = Group.objects.get_or_create(name=name)
    if created or not group.permissions.exists():
        group.permissions.set(_permissions_for(codenames))
    return group


def _permissions_for(codenames):
    wanted = [c.split(".", 1) for c in codenames]
    permissions = []
    for app_label, codename in wanted:
        permission = Permission.objects.filter(
            content_type__app_label=app_label, codename=codename
        ).first()
        if permission is not None:
            permissions.append(permission)
    return permissions


def apply_role(user, role, extra_groups=()):
    """Set staff/superuser flags and group membership to match ``role``.

    ``user`` must already be saved, because group membership needs a primary key.
    """
    role = Role(role)
    user.is_superuser = role == Role.ADMIN
    user.is_staff = role == Role.ADMIN
    user.save(update_fields=["is_superuser", "is_staff"])

    role_group_names = {name for name, _ in ROLE_GROUPS.values()}
    kept = [g for g in extra_groups if g.name not in role_group_names]
    user.groups.set([get_role_group(role), *kept])

    profile, _ = UserProfile.objects.get_or_create(user=user)
    if profile.role != role:
        profile.role = role
        profile.save(update_fields=["role"])
    return user


def is_registry_admin(user):
    """True when the account may manage other users."""
    return bool(user.is_authenticated and user.is_active and (user.is_superuser or user.is_staff))
