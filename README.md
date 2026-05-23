# Expense Tracker — Backend

[![CI](https://github.com/shubhamjain2998/expense-tracking-backend/actions/workflows/ci.yml/badge.svg)](https://github.com/shubhamjain2998/expense-tracking-backend/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Conventional Commits](https://img.shields.io/badge/conventional%20commits-1.0.0-fa6673.svg)](https://www.conventionalcommits.org)

> FastAPI backend for a personal finance app that ingests Indian bank-statement PDFs, auto-categorises transactions with fuzzy matching, supports expense splits between people, and exposes analytics for monthly and year-to-date spend against an annual budget.

---

## What is this?

A self-hosted REST API for tracking personal expenses against an **annual** budget. It is built for one user at a time (multi-tenant by `user_id`, but no team features) and is opinionated for the Indian context — INR-aware amounts, financial-year period mode (April → March), and parsers tuned for HDFC / Axis / SBI statement formats.

It is the backend half of a two-repository project. The frontend lives at **[shubhamjain2998/expense-tracking-frontend](https://github.com/shubhamjain2998/expense-tracking-frontend)** — see [Companion frontend](#companion-frontend) below.

## Highlights

- **PDF ingestion** — uploads are parsed in memory with `pdfplumber`; no statement file is ever written to disk.
- **Duplicate guard** — SHA-256 of the file body is recorded so the same statement cannot be imported twice.
- **Fuzzy auto-categorisation** — `RapidFuzz` matches each transaction description against learned `category_mappings` at ≥ 80% similarity.
- **Expense splits** — every processed transaction can be shared between people by percentage or fixed amount; per-share settlement is tracked.
- **Period-aware analytics** — endpoints accept `period_mode=calendar|fy` so the same `(year, month)` parameters work for both Jan–Dec and Apr–Mar reporting.
- **Cookie-first auth** — `httpOnly` cookie is the preferred path; `Authorization: Bearer` is still accepted for backwards compatibility.
- **Soft delete + restore** — raw transactions are never hard-deleted, so import history is auditable and recoverable.

## Tech stack

| Layer | Tool | Version |
|---|---|---|
| Web framework | FastAPI | `0.115.6` |
| ASGI server | Uvicorn | `0.32.1` |
| ORM | SQLAlchemy | `2.0.36` |
| Migrations | Alembic | `1.14.0` |
| Database | PostgreSQL (Supabase) | 15+ |
| PDF parsing | pdfplumber | `0.11.4` |
| Fuzzy matching | RapidFuzz | `3.10.1` |
| Validation | Pydantic | `2.10.3` |
| Auth | PyJWT + bcrypt | `2.12.1` / `4.2.1` |
| Tests | pytest | `8+` |
| Lint / format | flake8 + black | `7.3` / `25.11` |

Python 3.11 or newer is required.

## Quick start

```bash
git clone git@github.com:shubhamjain2998/expense-tracking-backend.git
cd expense-tracking-backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # then edit DATABASE_URL + SUPABASE_JWT_SECRET
alembic upgrade head
python server.py           # http://localhost:8000
```

Swagger UI is then available at <http://localhost:8000/docs>, ReDoc at <http://localhost:8000/redoc>, and a health probe at <http://localhost:8000/health>.

See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) for the full local-development walkthrough.

## Repository tour

```
.
├── app/                   # FastAPI application package
│   ├── main.py            #   entry point + middleware + router wiring
│   ├── config.py          #   pydantic-settings (env vars)
│   ├── database.py        #   SQLAlchemy engine + session factory
│   ├── models.py          #   all SQLAlchemy table definitions
│   ├── schemas.py         #   all Pydantic request / response models
│   ├── auth.py            #   JWT dependency (cookie or bearer)
│   ├── routers/           #   one file per domain (auth, budget, …)
│   └── services/          #   pdf_parser, text_parser, normalizer, period helpers
├── alembic/               # database migrations (versions/ holds the chain)
├── tests/                 # pytest suite + fixtures
├── scripts/               # one-off maintenance scripts
├── docs/                  # architecture, API, database, deployment, etc.
├── server.py              # uvicorn launcher for local dev
└── requirements.txt       # production dependencies
```

## How it works

```
Setup (once/year)   →   Ingest (monthly)   →   Process       →   Analyse
Configure budget        Upload PDF              Auto + manual     Dashboard
```

**Setup** — Define an annual budget: a list of categories (Groceries, Rent, Travel, …) with allocated monthly amounts, stored once in `budget_plans`.

**Ingest** — Accept a PDF bank or card statement, parse it in memory with `pdfplumber`, extract rows (date · description · amount), and save them to `raw_transactions` with `status=pending`.

**Process** — Review the raw table and soft-delete non-expense rows; then `POST /transactions/auto-categorise` runs RapidFuzz over learned mappings and pre-fills matches ≥ 80%; manually assign the rest with `POST /transactions/process`, optionally saving the mapping so it auto-applies next month.

**Analyse** — `/dashboard/*` returns budget vs. actual per category, monthly trend, year-to-date totals, and a split ledger showing each person's share of shared expenses.

A full architecture diagram lives in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Companion frontend

This API is consumed by the React + TypeScript SPA at **[shubhamjain2998/expense-tracking-frontend](https://github.com/shubhamjain2998/expense-tracking-frontend)**.

```
┌─────────────────────────┐      HTTPS / JSON      ┌─────────────────────────┐
│  Frontend (Vercel)      │ ─────────────────────▶ │  Backend (Render)       │
│  React + Vite + TS      │                        │  FastAPI + SQLAlchemy   │
└─────────────────────────┘                        └────────────┬────────────┘
                                                                │
                                                                ▼
                                                   ┌─────────────────────────┐
                                                   │  Database (Supabase)    │
                                                   │  PostgreSQL             │
                                                   └─────────────────────────┘
```

The two repositories are versioned independently. CORS is configured per the `FRONTEND_ORIGIN` env var, and auth tokens are exchanged via `httpOnly` cookies (preferred) or `Authorization: Bearer` (legacy).

## Documentation

| Document | What's inside |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System design, module breakdown, request lifecycle, key design decisions. |
| [`docs/API.md`](docs/API.md) | Curated endpoint reference grouped by domain; sample requests and responses. Points to Swagger for the full schema. |
| [`docs/DATABASE.md`](docs/DATABASE.md) | Mermaid ERD, table-by-table description, migrations workflow. |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Render + Supabase setup; env var reference; troubleshooting. |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | Local dev walkthrough; how to add an endpoint / model / migration. |
| [`docs/TESTING.md`](docs/TESTING.md) | pytest layout, fixtures, coverage expectations. |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | What's shipped, what's in-flight, what's planned. |
| [`docs/FAQ.md`](docs/FAQ.md) | Recurring questions a new contributor would ask. |
| [`docs/GLOSSARY.md`](docs/GLOSSARY.md) | Domain terms (raw vs. processed transaction, mapping, share, settlement, …). |
| [`docs/examples/api.http`](docs/examples/api.http) | Runnable requests for VS Code REST Client / JetBrains HTTP client. |
| [`CHANGELOG.md`](CHANGELOG.md) | Release history. |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Commit conventions, branch naming, PR checklist. |
| [`SECURITY.md`](SECURITY.md) | Vulnerability reporting policy. |
| [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) | Community standards. |

## Project status

Tagged `v1.0.0` is the latest release. Active development continues — see [`docs/ROADMAP.md`](docs/ROADMAP.md) for what's planned. Issues and discussions are open; see [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening a PR.

## License

[MIT](LICENSE) © 2026 Shubham Jain.
