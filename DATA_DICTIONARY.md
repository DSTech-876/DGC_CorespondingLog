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

The supplied extract also contains two additional blank `FROM` and `TO` headings. Their meaning must be confirmed against the original workbook before mapping them.
