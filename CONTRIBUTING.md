# Contributing

Thanks for taking the time to contribute. This guide covers the local setup, the commit conventions enforced by the project, and the PR checklist that mirrors what CI runs.

## Local setup

```bash
# 1. Clone and enter the repo
git clone git@github.com:shubhamjain2998/expense-tracking-backend.git
cd expense-tracking-backend

# 2. Create a virtual environment (Python 3.11+)
python -m venv venv
source venv/bin/activate         # macOS / Linux
# .\venv\Scripts\activate         # Windows PowerShell

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# edit .env: DATABASE_URL, SUPABASE_JWT_SECRET, FRONTEND_ORIGIN

# 5. Apply database migrations
alembic upgrade head

# 6. Install pre-commit hooks
pre-commit install

# 7. Run the dev server
python server.py
# or
uvicorn app.main:app --reload --port 8000
```

The API is then available at `http://localhost:8000` with auto-generated Swagger docs at `/docs` and ReDoc at `/redoc`.

See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) for the full local-development walkthrough including how to add an endpoint, a model, or a migration.

## Branch naming

| Type | Pattern |
|---|---|
| New feature | `feat/*` |
| Bug fix | `fix/*` |
| Refactoring | `refactor/*` |
| Chores / tooling | `chore/*` |
| Documentation | `docs/*` |
| CI / build | `ci/*` |
| Tests | `test/*` |

## Commit messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <lowercase subject>
```

Common scopes:

`auth` · `budget` · `uploads` · `transactions` · `categories` · `tags` · `persons` · `dashboard` · `admin` · `backup` · `models` · `schemas` · `services` · `migrations` · `tests` · `deps` · `docs` · `ci`

Examples:

```
feat(uploads): support multi-page HDFC Regalia statements
fix(transactions): classify refund rows correctly
chore(deps): bump fastapi to 0.116
test(auth): cover expired-token branch
```

## PR checklist

Before opening a pull request:

- [ ] `pre-commit run --all-files` passes (black + flake8)
- [ ] `pytest` passes locally
- [ ] New endpoints have at least one happy-path test
- [ ] New models include an Alembic migration (`alembic revision --autogenerate -m "..."`)
- [ ] Public schema changes are reflected in `docs/API.md` or `docs/DATABASE.md`
- [ ] `[Unreleased]` section of `CHANGELOG.md` updated if user-visible

CI runs the same checks on every PR — see [`.github/workflows/`](.github/workflows/).

## Code style

- Code is formatted by **black** and linted by **flake8** (88-character line length).
- Both run automatically via pre-commit on staged files; configure your editor to run them on save for the best experience.
- Comments only when the **why** is non-obvious: a hidden constraint, a subtle invariant, or a workaround for a specific external bug.
- Do not add comments that reference the current task, PR, or issue number — those belong in the PR description and rot as the code evolves.
- Public functions should have a one-line docstring describing what they return, not how.

## Testing

See [`docs/TESTING.md`](docs/TESTING.md) for the full testing guide. Quick reference:

| Command | Purpose |
|---|---|
| `pytest` | Run the full suite. |
| `pytest tests/test_upload_pipeline.py` | Run a single file. |
| `pytest -k "auto_categorise"` | Run tests matching a name. |
| `pytest -x` | Stop on first failure. |
| `pytest --lf` | Re-run only the last failures. |

## Database changes

Schema changes go through Alembic:

```bash
# After editing models in app/models.py:
alembic revision --autogenerate -m "add ignored_merchants table"
# Review the generated file under alembic/versions/
alembic upgrade head
```

Never edit a migration after it has been applied to a shared environment. Add a new migration that fixes whatever needs fixing.

## Releases

Releases follow [Semantic Versioning](https://semver.org/) and are published from `main` via annotated git tags (`vX.Y.Z`). Each release has a corresponding [GitHub Release](https://github.com/shubhamjain2998/expense-tracking-backend/releases) and a [`CHANGELOG.md`](CHANGELOG.md) entry — please move the relevant `[Unreleased]` notes into a new version section when cutting a release.
