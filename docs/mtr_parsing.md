# MTR Parsing and Verification Workflow

This document describes how Farraen parses Material Test Reports (MTRs), how verification works, and the operational steps needed to keep the workflow healthy.

## Overview

1. **Upload** – When a new `FileVersion` is uploaded for a Drive file whose `doc_type` is `MTR`, Farraen clears any previous approval metadata on the `FileNode` and enqueues the `welds.process_mtr_fileversion` Celery task.
2. **Parsing** – The task extracts page text with PyMuPDF. If a page contains little or no text, the task renders it to an image and runs Tesseract OCR (via Pillow and `pytesseract`). Page content is chunked and sent to OpenAI for structured extraction. Parsed materials are stored in `welds.MaterialHeatDraft` rows along with raw payload metadata.
3. **Verification** – Drafts appear in `/o/<org>/mtrs/drafts/`. Reviewers edit values using the standard `MaterialHeatForm`; saving a draft creates or updates the canonical `MaterialHeat`. Once all drafts for a file are marked verified, the related `FileNode` is automatically marked as MTR-approved.
4. **Manual approval** – Organization admins can approve an MTR manually from the Drive file preview. Manual approval records the approver and version but still warns about unverified drafts.
5. **Enforcement** – `MaterialHeat.save()` refuses to persist records that reference an unapproved MTR unless the save occurs from the verification flow. Forms and admin selectors only surface approved MTRs.

## New models & fields

- `drive.FileNode` now records approval metadata (`mtr_approved`, `mtr_approved_by`, `mtr_approved_at`, `mtr_approved_version`).
- `welds.MaterialHeatDraft` stores parser output awaiting human review, including raw OpenAI payloads, page numbers, and confidence scores.

## Environment variables

| Variable | Description |
| --- | --- |
| `OPENAI_API_KEY` | API key for OpenAI or Azure OpenAI deployments. Required for parsing. |
| `OPENAI_MODEL` | Chat completion model name (or Azure deployment name). |
| `OPENAI_API_BASE` | Azure OpenAI base URL (omit for public OpenAI). |
| `OPENAI_DEPLOYMENT` | Azure deployment identifier (if applicable). |
| `OPENAI_API_VERSION` | Azure API version (defaults to `2024-02-15-preview` when omitted). |
| `CELERY_BROKER_URL` | Celery broker (Redis) connection string, e.g. `redis://localhost:6379/0`. |
| `CELERY_RESULT_BACKEND` | Celery result backend (often the same Redis instance). |

Celery dispatch is attempted first. If no worker is available the signal handler logs the failure and runs the parser synchronously, which is acceptable for development but **not recommended** for production due to blocking behaviour.

## Dependencies

The workflow relies on the following new packages:

- `celery` and `redis` – queueing background parsing work.
- `PyMuPDF (fitz)` – PDF parsing and page rendering.
- `Pillow` and `pytesseract` – OCR fallback for scanned PDFs.
- `openai` – Structured extraction via Chat Completions.

Ensure system packages for Tesseract OCR are installed when deploying (e.g. `apt-get install tesseract-ocr` on Debian/Ubuntu).

## Running locally

1. Install dependencies: `pip install -r requirements.txt`.
2. Start Redis (for example `redis-server` in another terminal).
3. Run Celery in worker mode: `celery -A pipeledger worker -l info` (update module name if the Celery app lives elsewhere). In development, if Celery is not running the parser will execute synchronously.
4. Start Django: `python manage.py runserver`.
5. Upload an MTR via Drive; visit `/o/<org>/mtrs/drafts/` to review drafts.

## Retrying & troubleshooting

- Use the Drive preview to trigger manual approval or to confirm an MTR is ready. The preview banner links directly to the draft review list.
- If parsing fails, Celery retries transient `RuntimeError`s with exponential backoff. Permanent failures leave the MTR unapproved—re-upload the PDF or trigger reparsing by saving a new `FileVersion`.
- Draft rows are idempotent: parsing deletes existing drafts for the file before inserting new ones, so rerunning the task keeps a single set of drafts.

## Compliance notes

MTR data can contain sensitive material specifications. Confirm that your OpenAI deployment satisfies contractual and regulatory requirements (Azure OpenAI or private deployments are recommended for confidential data). When in doubt, disable Celery workers until the correct endpoint is configured—uploads will still clear approval but drafts will not be recreated until parsing succeeds.
