# Generated migration to backfill UserProfile for existing users

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_profiles_for_existing_users(apps, schema_editor):
    """Create a UserProfile for each existing User that doesn't have one."""
    User = apps.get_model('auth', 'User')
    UserProfile = apps.get_model('registry', 'UserProfile')
    
    for user in User.objects.all():
        UserProfile.objects.get_or_create(user=user)


def reverse_profiles(apps, schema_editor):
    """Delete all UserProfiles (reverse the forward operation)."""
    UserProfile = apps.get_model('registry', 'UserProfile')
    UserProfile.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('registry', '0002_userprofile'),
    ]

    operations = [
        migrations.RunPython(create_profiles_for_existing_users, reverse_profiles),
    ]
