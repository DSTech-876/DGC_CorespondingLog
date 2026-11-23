# Generated migration to sync officer delete_correspondence permission to existing database groups

from django.db import migrations


def add_delete_permission_to_officers(apps, schema_editor):
    """Add delete_correspondence permission to existing Records officers group."""
    from django.contrib.auth.models import Group, Permission
    
    group = Group.objects.filter(name="Records officers").first()
    if group is not None:
        # Get the delete_correspondence permission
        permission = Permission.objects.filter(
            content_type__app_label="registry", codename="delete_correspondence"
        ).first()
        if permission is not None and not group.permissions.filter(id=permission.id).exists():
            group.permissions.add(permission)


def reverse_delete_permission(apps, schema_editor):
    """Remove delete_correspondence permission from Records officers group."""
    from django.contrib.auth.models import Group, Permission
    
    group = Group.objects.filter(name="Records officers").first()
    if group is not None:
        # Get the delete_correspondence permission
        permission = Permission.objects.filter(
            content_type__app_label="registry", codename="delete_correspondence"
        ).first()
        if permission is not None:
            group.permissions.remove(permission)


class Migration(migrations.Migration):

    dependencies = [
        ('registry', '0011_actiondirective_action_comment'),
    ]

    operations = [
        migrations.RunPython(add_delete_permission_to_officers, reverse_delete_permission),
    ]
