# Architecture

This document describes how the backend is organised, the lifecycle of an incoming HTTP request, and the decisions behind the major dependency choices.

## System context

```
┌─────────────────────────────────────────────────────────────────┐
│                       Frontend (Vercel)                         │
│                    React + Vite + TypeScript                    │
└──────────────────────────────┬──────────────────────────────────┘
                               │  HTTPS / JSON
                               │  cookie or Bearer JWT
┌──────────────────────────────▼──────────────────────────────────┐
│                        Backend (Render)                         │
│                       FastAPI + SQLAlchemy                      │
│                                                                 │
│   /auth         register · login · logout · me                  │
│   /budget       annual budget plans per category                │
│   /uploads      PDF / text ingest → raw_transactions            │
│   /transactions raw review · auto-categorise · manual process   │
│   /categories   category list + rename + delete                 │
│   /category-    learned description → category rules            │
│       mappings                                                  │
│   /tags         user-defined labels                             │
│   /persons      expense-split participants                      │
│   /dashboard    summary · monthly-trend · multi-month · ytd     │
│                 split-ledger                                    │
│   /admin        bulk wipes (development convenience)            │
│   /backup       JSON export / import                            │
└──────────────────────────────┬──────────────────────────────────┘
                               │  pooled PG connection
┌──────────────────────────────▼──────────────────────────────────┐
│                      Database (Supabase)                        │
│                          PostgreSQL                             │
└─────────────────────────────────────────────────────────────────┘
```

## Module breakdown

```
app/
├── main.py             # FastAPI app, CORS, exception handler, router wiring
├── config.py           # pydantic-settings — loads env vars
├── database.py         # SQLAlchemy engine + sessionmaker + get_db dependency
├── auth.py             # get_current_user dependency (cookie or Bearer)
├── models.py           # SQLAlchemy table definitions (single file)
├── schemas.py          # Pydantic request / response models (single file)
├── routers/
│   ├── auth.py             # /auth/{register,login,logout,me}
│   ├── budget.py           # /budget — annual plans
│   ├── uploads.py          # /uploads/{statement,preview,text-import,preview-text}
│   ├── transactions.py     # /transactions/{raw,processed,auto-categorise,…}
│   ├── categories.py       # /categories
│   ├── category_mappings.py# /category-mappings
│   ├── tags.py             # /tags
│   ├── persons.py          # /persons
│   ├── dashboard.py        # /dashboard/{summary,monthly-trend,ytd,…}
│   ├── admin.py            # /admin/* (dev wipes)
│   └── backup.py           # /backup/{export,import}
└── services/
    ├── pdf_parser.py       # pdfplumber → list of (date, description, amount)
    ├── text_parser.py      # parse pasted statement text
    ├── normalizer.py       # clean descriptions (strip UPI refs, noise tokens)
    ├── period.py           # calendar ↔ financial year date helpers
    └── backup.py           # serialize / deserialize all user data
```

**Single-file `models.py` and `schemas.py`** are intentional. They are large but flat, which keeps the import graph trivial (no circular-import gymnastics) and makes "what tables exist?" and "what does the API accept / return?" both answerable in one file.

## Request lifecycle

```
1. ASGI request enters Uvicorn
2. CORSMiddleware                       (rejects disallowed origins)
3. ServerErrorMiddleware                (Starlette default)
4. FastAPI router dispatch              (matches path / method)
5. Dependency resolution:
     get_current_user(request, creds)   → reads cookie or Bearer header
                                        → verifies JWT (HS256, aud=authenticated)
                                        → returns user_id: uuid.UUID
     get_db()                           → yields a SQLAlchemy Session
6. Endpoint function runs
7. Response is serialized by Pydantic   (response_model=…)
8. CORS headers attached
9. Any uncaught Exception is intercepted by the catch-all
   exception handler in main.py so CORS headers still attach
   on 500 responses
```

Every protected endpoint declares `user_id: uuid.UUID = Depends(get_current_user)`. The dependency raises `401` if the token is missing, expired, or invalid. There is no role hierarchy — all authenticated users have the same permissions, scoped to their own `user_id`.

## Data flow: PDF → analytics

```
Bank PDF
   │
   ▼
POST /uploads/preview      (optional dry-run)
   │  pdfplumber parses in-memory, returns rows without persisting
   ▼
POST /uploads/statement
   │  SHA-256 of file → uploaded_files (409 if duplicate)
   │  rows → raw_transactions (status=pending)
   ▼
GET /transactions/raw
   │  user reviews; soft-deletes non-expense rows
   ▼
POST /transactions/auto-categorise
   │  RapidFuzz token_sort_ratio ≥ 80 against category_mappings
   │  creates processed_transactions for matches; raw rows → status=processed
   ▼
POST /transactions/process       (for the remainder)
   │  user picks category + optional tags + optional shares
   │  creates processed_transactions; raw row → status=processed
   ▼
GET /dashboard/*
   summary | monthly-trend | multi-month-summary | split-ledger | ytd
```

## Key design decisions

### Why FastAPI?
Native async-friendly, auto-generated OpenAPI docs at `/docs` and `/redoc`, and dependency-injection ergonomics that fit the per-request DB session + per-request auth pattern cleanly. Pydantic v2 integration removes the boilerplate of writing request / response serialisers by hand.

### Why SQLAlchemy 2.x (typed `Mapped`/`mapped_column`)?
The newer API gives proper typing on model attributes — `IDE` autocompletion works on relationships and queries, and `select()` with `.scalar_one_or_none()` makes "expect zero-or-one row" intent explicit at the call site rather than buried in error handling.

### Why Alembic over `Base.metadata.create_all`?
Schema drifts between local, staging, and Supabase need to be tracked deterministically. `alembic upgrade head` is the only schema change path; `create_all` would silently let an outdated dev DB diverge from prod.

### Why pdfplumber over PyPDF2 / pdfminer?
Bank statement PDFs use complex tables with varied cell positioning. `pdfplumber` exposes the layout primitives needed to extract rows reliably; `PyPDF2` only gives flowing text.

### Why RapidFuzz with token_sort_ratio ≥ 80?
Bank descriptions for the same merchant vary heavily (`SWIGGY*ORDER#1234`, `Swiggy Bangalore`, `SWIGGY LIMITED CR`). Token-sort handles word reordering, and 80% empirically separates real matches from coincidental string overlaps on the test corpus.

### Why dual cookie + Bearer auth?
Cookie auth (`httpOnly`, `Secure`, `SameSite=strict`) is the safer default — JS cannot exfiltrate it via XSS. The Bearer-header path is kept temporarily for backwards compatibility with older frontend builds that still rely on `localStorage`. New code paths should prefer the cookie.

### Why a catch-all `Exception` handler?
Starlette's `ServerErrorMiddleware` sits outside the CORS middleware. Without an explicit handler, an unhandled exception returns a `500` with **no CORS headers**, so the browser shows "blocked by CORS policy" instead of the real status. Routing 500s through a FastAPI handler ensures CORS headers attach in every case. See [`app/main.py`](../app/main.py).

### Why store both `amount` and `effective_amount` on a processed transaction?
`amount` is the total charged on the statement; `effective_amount` is what the current user actually owes after subtracting other people's shares. Storing both means the dashboard does not have to re-aggregate `transaction_person_shares` on every query, which keeps the analytics endpoints fast.

### Why is `users.password_hash` here even though Supabase manages auth elsewhere?
Historically the API issued its own JWTs signed with the Supabase shared secret. Passwords are hashed locally with bcrypt and never sent to Supabase. The shared secret is only used so that tokens minted by either side are interchangeable; this could be replaced with a backend-only secret if the dependency on Supabase Auth is ever dropped.

## Related documents

- [`DATABASE.md`](DATABASE.md) — schema details and ERD
- [`API.md`](API.md) — endpoint reference
- [`DEPLOYMENT.md`](DEPLOYMENT.md) — Render + Supabase setup
