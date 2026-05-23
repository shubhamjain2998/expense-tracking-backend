# FAQ

Recurring questions a new contributor or user might ask.

## Why is the budget annual, not monthly?

Real spending is uneven across the year — rent stays flat but travel spikes around holidays and groceries dip during travel months. Tracking annual budgets with monthly rollovers (planned) is more forgiving than per-month caps that constantly under- or over-shoot.

`allocated_amount` on `budget_plans` is therefore stored annually; the frontend divides by 12 only when it needs a monthly target for display.

## Why two auth paths — cookie and Bearer header?

Cookie auth (`httpOnly`, `Secure`, `SameSite=strict`) is safer because JavaScript cannot read the token, so XSS cannot exfiltrate it. The Bearer header is the older path; it remains supported temporarily so that frontend builds that still read `localStorage` keep working. New frontend code should prefer the cookie path.

## Why is `SUPABASE_JWT_SECRET` shared with Supabase?

So that tokens signed by either side are interchangeable. If you drop the Supabase Auth dependency later, you can replace this with any private 256-bit secret — nothing else has to change.

## What happens to raw transactions after I delete them?

They are soft-deleted: `status` becomes `deleted` and `deleted_at` is set, but the row stays in the table so it can be restored via `PATCH /transactions/raw/{id}/restore` and so import history remains auditable. `uploaded_files` is also kept for duplicate detection.

## What if I want to re-import a statement I deleted everything from?

Once *every* raw transaction tied to an `uploaded_files` row has been soft-deleted, the next upload of the same file body deletes the prior `uploaded_files` record automatically and re-imports cleanly. See [`app/routers/uploads.py`](../app/routers/uploads.py).

## Why does auto-categorise sometimes leave rows unmatched?

`RapidFuzz.token_sort_ratio` must score ≥ **80** against an existing `category_mapping`. Lower thresholds produce too many false positives (e.g. `"Amazon"` vs `"Amazon Prime"`), so the safer default is to leave ambiguous rows for manual review.

## Why are amounts `Numeric(12, 2)` and not `Float`?

Floating-point arithmetic loses pennies. INR amounts go up to lakhs and crores; `Numeric(12, 2)` covers up to ₹9,99,99,99,999.99 with zero rounding error and is the standard recommendation for money in Postgres.

## What's the difference between a "raw" and a "processed" transaction?

- **Raw** = "what came off the statement". One row per parsed PDF / text line. Can be soft-deleted, restored, or promoted.
- **Processed** = "what we count toward the budget". Has a category, optional tags, optional split shares, and an `effective_amount` (the share the current user actually owes after subtracting others' contributions).

See [`GLOSSARY.md`](GLOSSARY.md).

## Why are admin endpoints in the production build?

They are scoped to the calling user — `DELETE /admin/all` only wipes that user's rows. They are convenient for testing and for users who want to reset their own data. Restricting them to a dev build adds operational complexity without much real safety gain.

## How do I import or export my data?

`GET /backup/export` returns a JSON blob with every user-owned row. `POST /backup/import` accepts that same JSON. Useful for moving between Supabase projects or making manual backups.

## What Python version is required?

3.11 or newer. The codebase uses `from __future__ import annotations` and modern typing features in places; older versions may work but are untested.

## Where do I report a bug or request a feature?

- Bug: [Bug report issue template](https://github.com/shubhamjain2998/expense-tracking-backend/issues/new?template=bug_report.yml).
- Feature: [Feature request issue template](https://github.com/shubhamjain2998/expense-tracking-backend/issues/new?template=feature_request.yml).
- Security: see [`SECURITY.md`](../SECURITY.md) — please do **not** open a public issue.
- General discussion: [GitHub Discussions](https://github.com/shubhamjain2998/expense-tracking-backend/discussions).
