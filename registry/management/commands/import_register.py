from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from registry.importer import ImportError_, import_records, parse_rows, rows_from_path
from registry.models import Correspondence


class Command(BaseCommand):
    help = "Import a correspondence register from XLSX, CSV or Markdown table."

    def add_arguments(self, parser):
        parser.add_argument("file")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--skip-existing", action="store_true")
        parser.add_argument(
            "--direction",
            choices=[choice.value for choice in Correspondence.Direction],
            default=Correspondence.Direction.OTHER,
            help="Direction applied to every imported row (default: OTHER).",
        )

    def handle(self, *args, **opts):
        path = Path(opts["file"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")
        try:
            parsed = parse_rows(rows_from_path(path))
        except ImportError_ as exc:
            raise CommandError(str(exc)) from exc

        result = import_records(
            parsed,
            direction=opts["direction"],
            skip_existing=opts["skip_existing"],
            dry_run=opts["dry_run"],
        )
        if parsed.warning_records:
            self.stdout.write(self.style.WARNING(
                f"{len(parsed.warning_records)} row(s) had unreadable dates; "
                "original values are preserved in the source-row data."
            ))
        self.stdout.write(self.style.SUCCESS(result.summary))
