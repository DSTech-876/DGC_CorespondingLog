# Generated migration to synchronize role group permissions with ROLE_GROUPS definition.

from django.db import migrations


def sync_role_groups(apps, schema_editor):
    """Synchronize existing role groups with ROLE_GROUPS definition."""
    from django.contrib.auth.models import Group, Permission
    
    # Import ROLE_GROUPS to get the current definition
    from registry.roles import ROLE_GROUPS, _permissions_for
    
    for role, (group_name, codenames) in ROLE_GROUPS.items():
        try:
            group = Group.objects.get(name=group_name)
            # Update the group's permissions to match the current definition
            group.permissions.set(_permissions_for(codenames))
        except Group.DoesNotExist:
            # Group doesn't exist yet, it will be created when get_role_group() is called
            pass


def reverse_sync(apps, schema_editor):
    """Reverse migration (no-op since we're just updating existing groups)."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('registry', '0005_branch_position_remove_action_assigned_to_and_more'),
    ]

    operations = [
        migrations.RunPython(sync_role_groups, reverse_sync),
    ]
