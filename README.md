# Expense Tracker — Backend

FastAPI backend for a personal finance app that tracks monthly spending against an annual budget. Parses bank/card statement PDFs, auto-categorises transactions using fuzzy matching, supports expense splitting, and exposes analytics endpoints.

**Deployment target:** Backend → Render · Database → Supabase (PostgreSQL)

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        Frontend (Vercel)                     │
│                     React + Vite + TypeScript                │
└────────────────────────────┬─────────────────────────────────┘
                             │ HTTPS / REST
┌────────────────────────────▼─────────────────────────────────┐
│                       Backend (Render)                       │
│                     FastAPI + SQLAlchemy                     │
│                                                              │
│  /budget        budget plans per year                        │
│  /uploads       PDF ingest (pdfplumber, in-memory)           │
│  /transactions  raw review, auto-categorise, manual process  │
│  /categories    mapping management + category list           │
│  /persons       expense-split participants                   │
│  /dashboard     summary, trend, split-ledger, YTD            │
└────────────────────────────┬─────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────┐
│                     Database (Supabase)                      │
│                         PostgreSQL                           │
└──────────────────────────────────────────────────────────────┘
```

## Data Flow

```
Bank PDF
   │
   ▼
POST /uploads/statement
   │  pdfplumber parses in-memory
   ▼
raw_transactions (status=pending)
   │
   ├──► POST /transactions/auto-categorise
   │        RapidFuzz token_sort_ratio ≥ 80 against category_mappings
   │        → processed_transactions (status=processed)
   │
   └──► POST /transactions/process  (manual)
            category + split_count + person_ids
            → processed_transactions
            → transaction_persons (many-to-many)
                │
                ▼
          GET /dashboard/*
          summary | monthly-trend | split-ledger | ytd
```

---

## How it works

The API is organised around four phases:

```
Setup (once/year)   →   Ingest (monthly)   →   Process   →   Analyse
Configure budget        Upload PDF              Categorise      Dashboard
```

**Setup** — Define an annual budget: a list of categories (Groceries, Rent, Travel, …) with allocated monthly amounts. Stored once in `budget_plans`.

**Ingest** — Accept a PDF bank or card statement, parse it in-memory with `pdfplumber` (no file written to disk), extract rows (date · description · amount), and save them to `raw_transactions` with `status=pending`.

**Process** — Three steps:
1. Review the raw table and soft-delete any non-expense rows (transfers, payments, etc.).
2. Run auto-categorisation — RapidFuzz matches each description against known `category_mappings`. Matches ≥ 80% are pre-filled automatically.
3. Manually assign categories for unmatched rows. Optionally save the mapping so it auto-applies next month.

**Analyse** — Endpoints return budget vs. actual per category, monthly trend data, year-to-date totals, and a split ledger showing each person's share of shared expenses.

---

## Tech stack

| | |
|---|---|
| Runtime | Python 3.12 |
| Framework | FastAPI · Uvicorn |
| Validation | Pydantic v2 |
| ORM / migrations | SQLAlchemy 2 · Alembic |
| Database | PostgreSQL |
| PDF parsing | pdfplumber |
| Fuzzy matching | RapidFuzz |
| Analytics | Pandas |

---

## Project structure

```
backend/
├── app/
│   ├── main.py          FastAPI app + router registration
│   ├── models.py        SQLAlchemy ORM models
│   ├── database.py      DB connection / session
│   └── routers/
│       ├── budget.py        Phase 1 — budget plan CRUD
│       ├── uploads.py       Phase 2 — PDF ingestion
│       ├── transactions.py  Phase 3 — raw review + processing
│       ├── categories.py    Phase 3 — mapping management
│       ├── persons.py       Phase 3 — person management
│       └── dashboard.py     Phase 4 — analytics endpoints
├── alembic/             DB migration scripts
├── requirements.txt
└── .env.example
```

---

## Database schema

| Table | Purpose |
|---|---|
| `budget_plans` | Year · category · allocated amount |
| `raw_transactions` | Rows extracted from PDF; status: `pending` / `deleted` / `processed` |
| `processed_transactions` | Categorised transactions with effective amount after splitting |
| `category_mappings` | Description pattern → category; used for auto-categorisation |
| `persons` | People who can share expenses |
| `transaction_persons` | Many-to-many join between processed transactions and persons |

---

## API overview

### Phase 1 — Budget (`/budget`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/budget` | Create annual budget (year + category list) |
| `GET` | `/budget/{year}` | Fetch all entries for a year |
| `PUT` | `/budget/{id}` | Update a single entry |
| `DELETE` | `/budget/{id}` | Delete a single entry |

### Phase 2 — Ingest (`/uploads`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/uploads/statement` | Upload PDF; returns `{ inserted, skipped, rows }` |
| `POST` | `/uploads/preview` | Dry-run parse — returns what would be inserted without saving |

### Phase 3 — Process (`/transactions`, `/categories`, `/persons`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/transactions/raw` | List pending raw transactions |
| `DELETE` | `/transactions/raw/{id}` | Soft-delete a row |
| `PATCH` | `/transactions/raw/{id}/restore` | Restore a soft-deleted row |
| `POST` | `/transactions/auto-categorise` | Run RapidFuzz over all pending rows |
| `GET` | `/transactions/pending-manual` | Rows still needing manual assignment |
| `POST` | `/transactions/process` | Categorise a row; optionally split and save mapping |
| `PATCH` | `/transactions/processed/{id}` | Edit a processed transaction |
| `GET` | `/categories` | List all saved mappings |
| `GET` | `/categories/list` | Distinct category names (for dropdowns) |
| `DELETE` | `/categories/{id}` | Remove a mapping |
| `GET` | `/persons` | List all persons |
| `POST` | `/persons` | Add a person |
| `DELETE` | `/persons/{id}` | Remove a person (blocked if linked to transactions) |

### Phase 4 — Analyse (`/dashboard`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/dashboard/summary` | Budget vs. actual per category for a month |
| `GET` | `/dashboard/monthly-trend` | Month-by-month spend for a year (optionally by category) |
| `GET` | `/dashboard/split-ledger` | Per-person total for shared expenses |
| `GET` | `/dashboard/ytd` | Year-to-date totals per category |

---

## Local setup

**Prerequisites:** Python 3.12 · PostgreSQL

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Set DATABASE_URL=postgresql://user:password@host:5432/dbname

alembic upgrade head
uvicorn app.main:app --reload
```

API docs: `http://localhost:8000/docs`

---

## Development

### Pre-commit hooks

The repo uses [pre-commit](https://pre-commit.com/) with **black** (formatter) and **flake8** (linter).

Install once after cloning:

```bash
pre-commit install
```

Hooks run automatically on `git commit`. If black reformats any files, stage the changes and commit again.

### Creating migrations

After changing `app/models.py`:

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

---

## Key design decisions

**PDF never touches disk** — the file is parsed entirely in-memory by `pdfplumber` and discarded. Only the extracted rows are persisted.

**Fuzzy matching threshold of 80%** — RapidFuzz `token_sort_ratio` handles minor wording variations in merchant names without requiring exact matches.

**Soft deletes on raw transactions** — rows are marked `deleted` rather than removed, so nothing is lost if a row is accidentally deleted.

**Split expenses** — `effective_amount = amount / split_count`, so shared expenses are apportioned correctly in budget comparisons.

**Mappings auto-improve** — each time a mapping is used, `match_count` and `last_used` are updated, building a record of how reliable each pattern is over time.
