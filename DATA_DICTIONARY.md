# Data dictionary

The Correspondence model preserves the fields present in the provided register:
- date_received_sent
- date_of_document
- sender
- recipient
- copy_to_thru
- description
- external_reference
- supervisor_directives
- received_collected_by
- date_received_collected
- further_comments

Additional application fields:
- reference_id: generated internal identifier
- direction: incoming/outgoing/other; historical import defaults to other until verified
- original_row: source values retained as JSON for migration review
- created_by / created_at / updated_at: application metadata

The supplied workbook repeats the `FROM` and `TO` headings. The import maps the first pair to `sender`/`recipient`, falling back to the later pair only when the first is blank; the repeated columns are kept in `original_row` as `FROM (2)` and `TO (2)` until their meaning is confirmed.

Rows that only carry a section banner (`YEAR 2021`, `APRIL`, …) are treated as dividers and are not imported.
