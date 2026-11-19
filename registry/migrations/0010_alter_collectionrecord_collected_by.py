# Generated migration to widen CollectionRecord.collected_by field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('registry', '0009_correspondence_from_addressee_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='collectionrecord',
            name='collected_by',
            field=models.TextField(),
        ),
    ]
