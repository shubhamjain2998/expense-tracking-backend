# Roadmap

This file lists what is shipped, in-flight, and planned. The authoritative shipped-changes list lives in [`CHANGELOG.md`](../CHANGELOG.md); this document is the forward-looking complement.

## Shipped

See [`CHANGELOG.md`](../CHANGELOG.md) and the [Releases page](https://github.com/shubhamjain2998/expense-tracking-backend/releases).

The current latest is **`v1.0.0`**. Tags `v0.1.0` → `v0.9.0` cover the historical milestones (PDF ingest, first end-to-end API, splits + tags, multi-user JWT, data-hygiene round, FY mode + backup + QA round 1).

## In flight

Repo hardening (this pass):

- **PR 1** — README rewrite + `docs/` tree + repo-health metadata + `.github/` templates. ✅ Shipped.
- **PR 2** *(this PR)* — Backfill historical tags (`v0.1.0` → `v0.9.0`) with GitHub Releases; complete `CHANGELOG.md`.
- **PR 3** — `pyproject.toml`, pip-tools lockfile (`requirements.in` → hash-pinned `requirements.txt`), `pytest-cov` with a 70% coverage threshold.
- **PR 4** — GitHub Actions CI (Python 3.11 + 3.12 matrix, Postgres service container, `flake8` + `black --check` + `pytest --cov`); `pip-audit` and `bandit` in CI and pre-commit.
- **PR 5** — `commitizen` for conventional-commit enforcement and version bumping.

## Planned

### Reliability
- Branch protection on `main` (require CI pass + 1 review).
- A scheduled Render health check that wakes the free-tier service and reports its cold-start latency.
- Structured JSON logging in production with request IDs threaded through.

### Developer experience
- `release-please` (or `commitizen` automation) to open a release PR on every merge to `main`, so cutting a release becomes a single click.
- `CodeQL` analysis workflow — free for public repositories, surfaces SAST findings on every PR.
- Optional: migrate from `pip` + `pip-tools` to `uv` for ~10× faster installs.

### Product
- Multi-account import — track multiple cards / banks in one user account, distinguished by an `account_id` column.
- Recurring transaction detection — flag rows that match a learned monthly pattern (rent, subscriptions).
- Per-category monthly rollovers — under-spend in a category in month *N* increases its budget in month *N + 1*.
- Webhooks on processed transactions for downstream integrations.

### Documentation
- Architecture Decision Records (`docs/adr/`) for each non-obvious choice (FastAPI, dual auth, pdfplumber, fuzzy threshold).
- Per-router quickstarts — a paragraph per router file linking back to the curated reference in [`API.md`](API.md).

Open an [issue](https://github.com/shubhamjain2998/expense-tracking-backend/issues/new/choose) or [discussion](https://github.com/shubhamjain2998/expense-tracking-backend/discussions) to suggest additions or vote on priorities.
