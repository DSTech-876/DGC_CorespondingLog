import csv, re, json
from datetime import datetime
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from openpyxl import load_workbook
from registry.models import Correspondence

ALIASES = {
    "date_received_sent": ["DATE RECEIVED/SENT", "DATE RECEIVED / SENT"],
    "date_of_document": ["DATE OF DOCUMENT"],
    "sender": ["FROM"],
    "recipient": ["TO"],
    "copy_to_thru": ["COPY TO OR THRU", "COPY TO OR THRU'"],
    "description": ["DESCRIPTION"],
    "external_reference": ["LAB#/ REF #/VOUCHER#/INVOICE#", "LAB #/ REF #/VOUCHER#/INVOICE#", "LAB/REF/VOUCHER/INVOICE"],
    "supervisor_directives": ["SUPERVISOR'S DIRECTIVES/COMMENTS OR GENERAL NOTATIONS/ACTION TAKEN"],
    "received_collected_by": ["RECEIVED/COLLECTED BY"],
    "date_received_collected": ["DATE RECEIVED/COLLECTED BY"],
    "further_comments": ["FURTHER COMMENTS"],
}
DATE_FORMATS = ("%d-%b-%y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y")

def clean(s):
    return re.sub(r"\s+", " ", str(s or "").replace("\xa0", " ")).strip()

def parse_date(value):
    value = clean(value)
    if not value: return None
    for fmt in DATE_FORMATS:
        try: return datetime.strptime(value, fmt).date()
        except ValueError: pass
    return None

def norm(s):
    return re.sub(r"[^A-Z0-9]", "", clean(s).upper())

def rows_from_file(path):
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        ws = load_workbook(path, data_only=True, read_only=True).active
        return [[clean(c) for c in row] for row in ws.iter_rows(values_only=True)]
    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            return [[clean(c) for c in row] for row in csv.reader(f)]
    if suffix in (".md", ".markdown", ".txt"):
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        return [[clean(c.strip()) for c in line.strip().strip("|").split("|")] for line in lines if "|" in line]
    raise CommandError("Supported formats: .xlsx, .csv, .md, .markdown, .txt")

class Command(BaseCommand):
    help = "Import a correspondence register from XLSX, CSV or Markdown table."
    def add_arguments(self, parser):
        parser.add_argument("file")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--skip-existing", action="store_true")
    def handle(self, *args, **opts):
        path = Path(opts["file"])
        if not path.exists(): raise CommandError(f"File not found: {path}")
        rows = rows_from_file(path)
        header_i = next((i for i, row in enumerate(rows) if any("DATERECEIVED" in norm(c) for c in row)), None)
        if header_i is None: raise CommandError("Could not identify the register header row.")
        headers = [norm(c) for c in rows[header_i]]
        indexes = {}
        for field, names in ALIASES.items():
            wanted = {norm(n) for n in names}
            indexes[field] = next((i for i, h in enumerate(headers) if h in wanted), None)
        imported = skipped = errors = 0
        for row in rows[header_i+1:]:
            if not any(clean(c) for c in row): continue
            vals = {}
            for field, idx in indexes.items():
                vals[field] = clean(row[idx]) if idx is not None and idx < len(row) else ""
            if not vals.get("description") or norm(vals.get("description")) in {"YEAR2021", "APRIL"}:
                continue
            original = {headers[i] if i < len(headers) else f"column_{i+1}": clean(v) for i, v in enumerate(row) if clean(v)}
            date1, date2 = parse_date(vals.pop("date_received_sent")), parse_date(vals.pop("date_of_document"))
            date3 = parse_date(vals.pop("date_received_collected"))
            key = (date1, vals.get("sender",""), vals.get("recipient",""), vals.get("description",""))
            if opts["skip_existing"] and Correspondence.objects.filter(date_received_sent=key[0], sender=key[1], recipient=key[2], description=key[3]).exists():
                skipped += 1; continue
            if not opts["dry_run"]:
                Correspondence.objects.create(
                    direction=Correspondence.Direction.OTHER,
                    date_received_sent=date1, date_of_document=date2,
                    date_received_collected=date3, original_row=original, **vals
                )
            imported += 1
        self.stdout.write(self.style.SUCCESS(f"{'Would import' if opts['dry_run'] else 'Imported'}: {imported}; skipped: {skipped}; errors: {errors}"))
