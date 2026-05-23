# Glossary

Domain terms used in the code, schemas, and API.

### Annual budget
The `allocated_amount` on a `budget_plans` row. Always stored annually; the frontend converts to monthly only for display.

### Auto-categorise
The `POST /transactions/auto-categorise` flow: walk every pending raw transaction, find the highest-scoring `category_mapping` whose `description_pattern` matches with `RapidFuzz.token_sort_ratio ≥ 80`, and promote that row to `processed_transactions` using the matched category.

### Calendar mode
The default period mode. `year=2025, month=4` → April 2025 → 1 Apr – 30 Apr. Contrast with [Financial year mode](#financial-year-mode).

### Category
A small, user-chosen bucket used for budgeting (e.g. *Groceries*, *Rent*, *Travel*). Stored case-folded in `categories.name`; rendered title-cased by the frontend. Renaming is in-place — referencing rows (`budget_plans`, `category_mappings`, `processed_transactions`) are unaffected.

### Category mapping
A learned rule of the form *"description matching pattern X → category Y"*. Created implicitly when you process a raw transaction with `save_mapping: true`. Stored in `category_mappings` with usage counters (`match_count`, `last_used`).

### Description (raw vs. cleaned)
- The **raw** description is the merchant string as it appears on the statement (`SWIGGY*ORDER#1234`).
- The **cleaned** description is what fuzzy matching compares against — punctuation and noise words (`UPI`, `NEFT`, `TXN`, `REF`) stripped by [`app/services/normalizer.py`](../app/services/normalizer.py).

### Effective amount
On `processed_transactions`, `effective_amount = amount - sum(share_amount for s in shares)`. It is the portion the current user actually owes after subtracting others' shares. Stored denormalised so the analytics endpoints do not need to re-aggregate `transaction_person_shares` on every query.

### Financial year mode
The Indian fiscal-year period. `year=2025` is FY 25-26 → 1 Apr 2025 – 31 Mar 2026; `month=1` is April; `month=12` is March (of the following calendar year). Toggled via `?period_mode=fy` on dashboard endpoints.

### Mapping (informal)
Shorthand for [Category mapping](#category-mapping).

### Person
Someone you split expenses with. Stored in `persons`. Has no login of their own — they exist as splits / settlements only.

### Processed transaction
A row in `processed_transactions`. Has a category, optional tags, optional split shares, and the computed `effective_amount`. This is the table all analytics endpoints read from.

### Raw transaction
A row in `raw_transactions`. The unprocessed parse of one statement line; status is `pending`, `processed`, or `deleted`. Soft-deleted rows stay in the table.

### Settled
The `transaction_person_shares.settled` boolean. Means the other person has paid you back (or you have paid them, depending on direction). Toggling does not delete or hide the share — it only flips the flag, so the split ledger can show outstanding balances.

### Share
One row in `transaction_person_shares` — a single person's contribution to one processed transaction. `share_type` is `percentage` (0–100) or `amount` (currency). `share_amount` is always the resolved currency value.

### Share split
The whole set of shares attached to a processed transaction. A transaction is "split" if it has any shares; otherwise it belongs entirely to the user.

### Soft delete
The pattern used for raw transactions only — `status='deleted'` + `deleted_at` timestamp, no row removal. Restorable via `PATCH /transactions/raw/{id}/restore`. Other tables use hard delete with FK-conflict protection.

### Tag
A user-defined label orthogonal to categories. A transaction can have any number of tags (or none). Used for cross-cutting filters on the dashboard (e.g. *birthday*, *work-reimbursable*).

### Transaction type (`txn_type`)
One of `expense`, `income`, `refund`, `transfer`. Refunds and transfers are excluded from spend totals by the analytics endpoints; income is summed separately. Auto-classified from the description on insert and editable via `PATCH /transactions/processed/{id}`.

### Uploaded file
A SHA-256 fingerprint of an imported PDF or pasted text blob, stored in `uploaded_files`. Used to reject duplicate imports with `409 Conflict`.

### User
A registered account. Identified by `id: UUID` everywhere; email is the natural key for login only. Every domain table has a `user_id` and queries always scope by it.
