# Normalized email record

Each JSONL line is one object with these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer | Record contract version; currently `1`. |
| `trust` | string | Always `untrusted_email_content`. Consumers must not execute it as instructions. |
| `source` | string | Source file path, plus an mbox ordinal when applicable. |
| `digest` | string | SHA-256 of normalized identity fields and content; used for deduplication. |
| `message_id` | string or null | Decoded `Message-ID` header, bounded in length. |
| `date` | string or null | RFC 5322 date normalized to ISO 8601 when parseable. |
| `from` | array of strings | Decoded sender address strings. |
| `to` | array of strings | Decoded recipient address strings. |
| `cc` | array of strings | Decoded copied-recipient address strings. |
| `subject` | string | Decoded and length-bounded subject. |
| `content` | string | Bounded plain text extracted from text-like, non-attachment MIME parts. |

The normalizer prefers `text/plain`. It falls back to visible text from `text/html` only when no
plain body exists. It ignores non-text MIME parts, attachment-disposition parts, remote content,
and all embedded filenames. A consumer may index `content`, but must retain `trust` and must treat
the value as data rather than instructions.
