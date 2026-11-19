from django.core.management.base import BaseCommand
from django.utils import timezone

from registry.models import CollectionRecord, Correspondence


class Command(BaseCommand):
    help = (
        "Create a Collection history entry for already-imported correspondence "
        "that has a 'Received / collected by' value but no matching "
        "CollectionRecord yet."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many records would be created without saving them.",
        )

    def handle(self, *args, **opts):
        dry_run = opts["dry_run"]
        candidates = (
            Correspondence.objects
            .exclude(received_collected_by="")
            .filter(collection_records__isnull=True)
        )

        records = [
            CollectionRecord(
                correspondence=item,
                collected_by=item.received_collected_by,
                collection_date=(
                    item.date_received_collected
                    or item.date_received_sent
                    or timezone.localdate()
                ),
            )
            for item in candidates
        ]

        if not dry_run and records:
            CollectionRecord.objects.bulk_create(records)

        verb = "Would create" if dry_run else "Created"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(records)} collection record(s)."))
