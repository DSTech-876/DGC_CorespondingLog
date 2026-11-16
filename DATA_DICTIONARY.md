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
- origin: internal/external/other; chosen on the registration form to record where the correspondence originated from
- original_row: source values retained as JSON for migration review
- created_by / created_at / updated_at: application metadata
- sender_branch / sender_position: branch and position of the "From" party, chosen from admin-managed `Branch`/`Position` lists (optional, added alongside `sender`)
- recipient_branch / recipient_position: branch and position of the "To" party, chosen from admin-managed `Branch`/`Position` lists (optional, added alongside `recipient`)
- current_status: computed, not stored — the status of the latest logged action, or "Not Addressed" when no action has been recorded yet; shown in place of the reference column on the register list

Branch and Position are simple admin-managed lookup lists (name only) that populate the From/To branch and position dropdowns on the Correspondence form; administrators add or edit entries from the Django admin.

Actions & directives (the `Action` model) record the directive text, its date and status — no From/To fields:
- status: open / pending / completed / closed — completed actions are shown with a verified checkmark icon, closed actions with a plain close icon, pending actions with a clock icon. Existing actions can be edited (including status) by users with the `change_action` permission.

The supplied workbook repeats the `FROM` and `TO` headings. The import maps the first pair to `sender`/`recipient`, falling back to the later pair only when the first is blank; the repeated columns are kept in `original_row` as `FROM (2)` and `TO (2)` until their meaning is confirmed.

Rows that only carry a section banner (`YEAR 2021`, `APRIL`, …) are treated as dividers and are not imported.
