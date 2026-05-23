# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Repository-health metadata: `LICENSE`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CONTRIBUTING.md`, `.editorconfig`, `.gitattributes`.
- `.github/` templates: `CODEOWNERS`, `dependabot.yml`, `PULL_REQUEST_TEMPLATE.md`, `ISSUE_TEMPLATE/{bug_report,feature_request,config}`.
- `docs/` tree: `ARCHITECTURE.md`, `API.md`, `DATABASE.md` (with Mermaid ERD), `DEPLOYMENT.md`, `DEVELOPMENT.md`, `TESTING.md`, `ROADMAP.md`, `FAQ.md`, `GLOSSARY.md`, `examples/api.http`.
- Module-level docstrings on the FastAPI entry point and the router / service packages.
- Backfilled annotated tags `v0.1.0` → `v0.9.0` with matching GitHub Releases for the historical milestones that pre-dated `v1.0.0`.
- `pyproject.toml` — project metadata, black config, pytest config (moved from `pytest.ini`), and `[tool.coverage]` config.
- `requirements.in` + `requirements-dev.in` for [pip-tools](https://github.com/jazzband/pip-tools); compiled to hash-locked `requirements.txt` + `requirements-dev.txt`.
- `pytest-cov` — coverage measurement on `app/` with `fail_under = 40` enforced (long-term target 70+).
- `.github/workflows/ci.yml` — GitHub Actions CI on `push` to `main` and every PR. Matrix runs against Python 3.11 + 3.12: `black --check` → `flake8` → `pytest --cov` → coverage HTML artifact → `bandit -r app`. `pip-audit -r requirements.txt --strict` runs non-blocking (pre-existing vulnerabilities to be addressed in a follow-up dependency-upgrade PR).
- `.flake8` — moved flake8 config out of `.pre-commit-config.yaml` so CI and pre-commit share one source of truth.
- `[tool.bandit]` block in `pyproject.toml`; new `bandit` hook in `.pre-commit-config.yaml`.
- CI status badge in `README.md`.
- [`commitizen`](https://commitizen-tools.github.io/commitizen/) — Conventional-Commits validation via a `commit-msg` pre-commit hook, plus `cz bump` for one-command version bumping and tagging. `[tool.commitizen]` in `pyproject.toml`; release workflow documented in `CONTRIBUTING.md`.
- `.github/workflows/lockfile.yml` — manually-triggered workflow that regenerates `requirements.txt` and `requirements-dev.txt` on Linux Python 3.11 (the platform CI installs against) and commits the result back to the branch it was triggered from. Necessary because the maintainer's local venv is Py3.9 on macOS, where pip cannot see Py3.10+-only conditional constraints.

### Fixed
- `tests/test_auth_cookie.py` — 6 tests were failing on `main` because their `"pw"` / `"hunter2"` passwords pre-dated the `v1.0.0` `min_length=8` rule. Bumped to 8+ char passwords so the full suite passes.

### Changed
- `README.md` rewritten as a landing page with badges, quick-start, repository tour, and a documentation index. Links across to the companion frontend repository.
- `v1.0.0` tag moved to the actual production-ready commit (cookie auth + validation hardening). The previous placeholder pointed at an early "docs: update readme" commit with no release notes.
- Dependency installation: contributors now run `pip-sync requirements-dev.txt`. `requirements.txt` is **generated** (`linguist-generated=true`); do not edit by hand. Render's build path stays `pip install -r requirements.txt`.

### Removed
- `pytest.ini` — config moved to `[tool.pytest.ini_options]` in `pyproject.toml`.

## [1.0.0] — 2026-05-22

First stable release. Auth becomes safer by default and input validation is tightened across the board.

### Added
- Dual-mode auth — `httpOnly` `Secure` cookie is preferred; `Authorization: Bearer` remains accepted for backwards compatibility.
- `POST /auth/logout` clears the cookie.
- `GET /auth/me` returns `{id, email}` for the caller.
- pytest tests for length caps and case-fold normalisation (group F).

### Changed
- Pydantic schemas enforce `min_length=1` + `max_length=64` on category, tag, and person names.
- `/auth/register` validates email format and password length server-side, not just at the schema layer.

### Fixed
- `500` responses now carry CORS headers — without the catch-all `Exception` handler in `app/main.py`, Starlette's `ServerErrorMiddleware` returned a bare 500 and the browser surfaced a misleading "blocked by CORS policy" error.

## [0.9.0] — 2026-05-22

Production-readiness round one.

### Added
- `period_mode=calendar|fy` on dashboard endpoints — Indian financial-year reporting (Apr → Mar).
- `/backup/export` and `/backup/import` — full JSON dump of all user-owned data, portable across user accounts.
- `pytest` test suite scaffold with the first QA-session PDF fixture (`tests/fixtures/april_regalia.pdf`).
- Regression tests for upload-pipeline findings (group B).

### Fixed
- **qa-2.4** — preview / upload now expose every skipped row, not just a count.
- **qa-2.5** — incomplete trailing parenthetical in merchant descriptions is stripped.
- **qa-2.6** — `clean_description` applied in preview endpoints to match the persisted path.
- **qa-2.8** — duplicate upload returns `409` cleanly (no longer raises `MultipleResultsFound`).
- **qa-3.1 / 3.2 / 3.4** — refund / transfer classification, `include_deleted` query param, accompanying migration.
- **qa-E** — calendar-month consistency in monthly-trend + new batch dashboard endpoint.

## [0.8.0] — 2026-04-24

Fuzzy matching and tag UX get sharper.

### Added
- `services/normalizer.py` — strips digits, punctuation, and noise tokens (UPI / NEFT / TXN / REF / channel markers) before fuzzy matching.
- Tag filtering on `GET /transactions/processed`.
- `POST /transactions/processed/bulk-tag` — apply or remove tags across many rows at once.
- `scripts/backfill_clean_descriptions.py` — backfill the normaliser over historical rows.
- `drop_db.sh` — local-dev convenience script.

### Changed
- `/transactions/auto-categorise` matches against normalised descriptions on both sides for better recall.

## [0.7.0] — 2026-04-24

Schema and ingestion get more robust.

### Added
- `TimestampMixin` — `created_at` / `updated_at` on every domain table.
- Composite indexes — `(user_id, status)` on raw, `(user_id, year, month)` and `(category_id, txn_date)` on processed.
- `UploadedFile` model — SHA-256 of every imported file/text blob.
- Duplicate-import detection — re-uploading the same statement returns `409 Conflict`.
- Soft-delete (`status='deleted'` + `deleted_at`) on raw transactions.

### Changed
- `passlib` replaced with direct `bcrypt`.
- Database session now rolls back on exception inside `get_db`.
- `clean_description` applied at upload time so stored rows are pre-normalised.

## [0.6.0] — 2026-04-15

The API moves from single-tenant to multi-user.

### Added
- Local email/password registration and login, issuing JWTs (HS256).
- Multi-user foundation — every domain table now carries `user_id` and every query is scoped by the calling user.
- `get_current_user` dependency that accepts the Supabase-signed JWT so tokens minted by either side are interchangeable.

## [0.5.0] — 2026-04-15

The data model gets richer.

### Added
- `transaction_person_shares` — split a transaction across multiple people, by percentage or amount.
- `tags` and `transaction_tags` — many-to-many free-form labels on processed transactions.
- `notes` column on `processed_transactions`.
- `/uploads/text-import` and `/uploads/preview-text` — paste a statement instead of uploading a PDF.
- Skipped-row reporting on the upload pipeline so users can see what was dropped and why.

### Changed
- Category strings on transactions replaced with FK to the `categories` table.

## [0.4.0] — 2026-04-06

Hardening for early frontend integration.

### Added
- `/admin/*` bulk-delete endpoints scoped to the caller.
- CORS middleware for browser-based clients.
- Processed-transaction endpoints split out from raw.

### Changed
- Categories are normalised to lowercase on create / rename / lookup.
- Uploads migration made idempotent so re-running it locally does not fail.

## [0.3.0] — 2026-04-05

The first version that supports the full ingest → analyse cycle.

### Added
- **Phase 3** — `/transactions`, `/categories`, `/persons` endpoints including auto-categorisation against `category_mappings`.
- **Phase 4** — `/dashboard/*` analytics endpoints (summary, monthly-trend, ytd, split-ledger).

### Docs
- README updated to describe the four operational phases.

## [0.2.0] — 2026-03-18

Budget management and bank-statement parsing land.

### Added
- `pre-commit` configuration with `black` and `flake8`.
- `/budget` CRUD endpoints (create, list, update, delete).
- `/uploads/statement` — parse bank-statement PDFs with `pdfplumber` and persist rows to `raw_transactions`.

## [0.1.0] — 2026-03-16

First runnable repository state.

### Added
- FastAPI application package with `app/main.py`, `app/config.py`, `app/database.py`.
- SQLAlchemy 2.x `Base` and `get_db` dependency.
- Alembic configured for migrations.
- Initial project README.

[Unreleased]: https://github.com/shubhamjain2998/expense-tracking-backend/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/shubhamjain2998/expense-tracking-backend/compare/v0.9.0...v1.0.0
[0.9.0]: https://github.com/shubhamjain2998/expense-tracking-backend/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/shubhamjain2998/expense-tracking-backend/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/shubhamjain2998/expense-tracking-backend/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/shubhamjain2998/expense-tracking-backend/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/shubhamjain2998/expense-tracking-backend/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/shubhamjain2998/expense-tracking-backend/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/shubhamjain2998/expense-tracking-backend/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/shubhamjain2998/expense-tracking-backend/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/shubhamjain2998/expense-tracking-backend/releases/tag/v0.1.0
