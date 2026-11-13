"""Shared parsing/loading logic for correspondence register imports.

The same code backs both the ``import_register`` management command and the
web import screen, so a file uploaded through the browser is interpreted
exactly like one imported from the server console.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from django.db import transaction

from .models import Correspondence

#: Register column -> spreadsheet headings that may carry it.
ALIASES = {
    "date_received_sent": ["DATE RECEIVED/SENT", "DATE RECEIVED / SENT"],
    "date_of_document": ["DATE OF DOCUMENT"],
    "sender": ["FROM"],
    "recipient": ["TO"],
    "copy_to_thru": ["COPY TO OR THRU", "COPY TO OR THRU'"],
    "description": ["DESCRIPTION"],
    "external_reference": [
        "LAB#/ REF #/VOUCHER#/INVOICE#",
        "LAB #/ REF #/VOUCHER#/INVOICE#",
        "LAB/REF/VOUCHER/INVOICE",
    ],
    "supervisor_directives": [
        "SUPERVISOR'S DIRECTIVES/COMMENTS OR GENERAL NOTATIONS/ACTION TAKEN"
    ],
    "received_collected_by": ["RECEIVED/COLLECTED BY"],
    "date_received_collected": ["DATE RECEIVED/COLLECTED BY"],
    "further_comments": ["FURTHER COMMENTS"],
}

DATE_FIELDS = ("date_received_sent", "date_of_document", "date_received_collected")

DATE_FORMATS = (
    "%d-%b-%y", "%d-%b-%Y", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
    "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%b %d, %Y", "%B %d, %Y",
    "%b. %d, %Y", "%d %b %Y", "%d %B %Y",
)

SUPPORTED_SUFFIXES = (".xlsx", ".csv", ".md", ".markdown", ".txt")

#: Month/year banner rows used as visual dividers inside the workbook.
DIVIDER_RE = re.compile(
    r"^(YEAR\s*\d{4}|\d{4}|JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST"
    r"|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)$",
    re.IGNORECASE,
)

#: Excel stores dates as days since 1899-12-30 on the Windows epoch.
EXCEL_EPOCH = date(1899, 12, 30)

PREVIEW_LIMIT = 25


class ImportError_(Exception):
    """Raised when a register file cannot be read or understood."""


def clean(value):
    """Normalise a spreadsheet cell to a trimmed single-spaced string."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def norm(value):
    """Comparison key for headings: upper case, letters and digits only."""
    return re.sub(r"[^A-Z0-9]", "", clean(value).upper())


def parse_date(value):
    """Best-effort date parsing for the mixed formats used in the register."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Excel serial numbers survive as numbers when a cell is not formatted.
        try:
            return EXCEL_EPOCH + timedelta(days=int(value))
        except (OverflowError, ValueError):
            return None
    text = clean(value)
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def rows_from_stream(stream, filename):
    """Read ``stream`` into a list of raw cell rows based on ``filename``.

    Values are returned untouched so date cells keep their native type.
    """
    suffix = Path(filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ImportError_(
            "Unsupported file type. Use one of: " + ", ".join(SUPPORTED_SUFFIXES)
        )
    data = stream.read()
    if not data:
        raise ImportError_("The selected file is empty.")
    if suffix == ".xlsx":
        from openpyxl import load_workbook

        try:
            workbook = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        except Exception as exc:  # openpyxl raises a wide range of errors
            raise ImportError_(f"The workbook could not be opened: {exc}") from exc
        worksheet = workbook.active
        rows = [list(row) for row in worksheet.iter_rows(values_only=True)]
        workbook.close()
        return rows
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    if suffix == ".csv":
        return [list(row) for row in csv.reader(io.StringIO(text, newline=""))]
    return [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in text.splitlines()
        if "|" in line
    ]


def rows_from_path(path):
    path = Path(path)
    with path.open("rb") as handle:
        return rows_from_stream(handle, path.name)


def find_header_row(rows):
    """Index of the heading row, identified by its ``DATE RECEIVED`` column."""
    for index, row in enumerate(rows):
        if any("DATERECEIVED" in norm(cell) for cell in row):
            return index
    return None


def header_labels(header_row):
    """Readable, de-duplicated column labels used for ``original_row`` keys."""
    labels, seen = [], {}
    for position, cell in enumerate(header_row):
        label = clean(cell) or f"column_{position + 1}"
        seen[label] = seen.get(label, 0) + 1
        labels.append(label if seen[label] == 1 else f"{label} ({seen[label]})")
    return labels


def map_columns(header_row):
    """Map model fields onto column indexes, keeping duplicate columns.

    The workbook repeats ``FROM``/``TO``; the first pair is authoritative and
    any later pair is used only when the first is blank.
    """
    normalised = [norm(cell) for cell in header_row]
    mapping = {}
    for field_name, names in ALIASES.items():
        wanted = {norm(name) for name in names}
        mapping[field_name] = [i for i, head in enumerate(normalised) if head in wanted]
    return mapping


@dataclass
class ParsedRecord:
    row_number: int
    values: dict
    original_row: dict
    warnings: list = field(default_factory=list)
    duplicate: bool = False

    @property
    def key(self):
        return (
            self.values["date_received_sent"],
            self.values["sender"],
            self.values["recipient"],
            self.values["description"],
        )


@dataclass
class ParsedFile:
    records: list
    headers: list
    unmapped_fields: list
    divider_rows: int = 0
    blank_rows: int = 0

    @property
    def warning_records(self):
        return [record for record in self.records if record.warnings]


def parse_rows(rows):
    """Turn raw spreadsheet rows into :class:`ParsedRecord` objects."""
    header_index = find_header_row(rows)
    if header_index is None:
        raise ImportError_(
            "Could not identify the register header row. The file must contain a "
            "'DATE RECEIVED/SENT' column heading."
        )
    header_row = rows[header_index]
    labels = header_labels(header_row)
    columns = map_columns(header_row)
    unmapped = [name for name, indexes in columns.items() if not indexes]

    parsed = ParsedFile(records=[], headers=labels, unmapped_fields=unmapped)
    for offset, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        cells = [clean(cell) for cell in row]
        if not any(cells):
            parsed.blank_rows += 1
            continue

        if all(cell == "---" for cell in cells if cell):
            parsed.divider_rows += 1
            continue

        values, raw_values = {}, {}
        for field_name, indexes in columns.items():
            text, raw = "", None
            for index in indexes:
                if index < len(row) and clean(row[index]):
                    text, raw = clean(row[index]), row[index]
                    break
            values[field_name] = text
            raw_values[field_name] = raw

        description = values.get("description", "")
        has_other_values = any(
            values[name] for name in values if name != "description"
        )
        if not has_other_values and (not description or DIVIDER_RE.match(description)):
            parsed.divider_rows += 1
            continue
        if not description and not has_other_values:
            parsed.blank_rows += 1
            continue

        record = ParsedRecord(
            row_number=offset,
            values=values,
            original_row={
                labels[i] if i < len(labels) else f"column_{i + 1}": cell
                for i, cell in enumerate(cells)
                if cell
            },
        )
        for field_name in DATE_FIELDS:
            text = values[field_name]
            parsed_date = parse_date(raw_values[field_name])
            record.values[field_name] = parsed_date
            if text and parsed_date is None:
                record.warnings.append(
                    f"{field_name.replace('_', ' ')}: could not read the date "
                    f"'{text}' — the original value is preserved."
                )
        parsed.records.append(record)
    return parsed


def existing_keys():
    """Duplicate-detection keys for everything already in the register."""
    return {
        tuple(values)
        for values in Correspondence.objects.values_list(
            "date_received_sent", "sender", "recipient", "description"
        )
    }


def flag_duplicates(parsed):
    """Mark records matching the register or an earlier row in the same file."""
    seen = existing_keys()
    for record in parsed.records:
        if record.key in seen:
            record.duplicate = True
        else:
            seen.add(record.key)
    return parsed


@dataclass
class ImportResult:
    created: int = 0
    skipped: int = 0
    warnings: int = 0
    dry_run: bool = False

    @property
    def summary(self):
        verb = "Would import" if self.dry_run else "Imported"
        return (
            f"{verb}: {self.created}; skipped duplicates: {self.skipped}; "
            f"rows with warnings: {self.warnings}"
        )


def import_records(parsed, *, user=None, direction=Correspondence.Direction.OTHER,
                   skip_existing=True, dry_run=False, batch_size=500):
    """Persist parsed records, returning counts for the caller to report."""
    flag_duplicates(parsed)
    result = ImportResult(dry_run=dry_run)
    pending = []

    def flush():
        if not pending:
            return
        created = Correspondence.objects.bulk_create(pending, batch_size=batch_size)
        for item in created:
            item.reference_id = f"COR-{item.created_at:%Y}-{item.pk:06d}"
        Correspondence.objects.bulk_update(created, ["reference_id"], batch_size=batch_size)
        pending.clear()

    with transaction.atomic():
        for record in parsed.records:
            if record.warnings:
                result.warnings += 1
            if skip_existing and record.duplicate:
                result.skipped += 1
                continue
            result.created += 1
            if dry_run:
                continue
            pending.append(
                Correspondence(
                    # bulk_create cannot run the per-instance numbering in
                    # ``save()``; a unique placeholder is replaced right after.
                    reference_id=f"TMP-{uuid4().hex[:20]}",
                    direction=direction,
                    original_row=record.original_row,
                    created_by=user,
                    **record.values,
                )
            )
            if len(pending) >= batch_size:
                flush()
        flush()
    return result
