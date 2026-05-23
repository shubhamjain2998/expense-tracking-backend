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

# 3. Install dependencies (production + dev tooling)
pip install -r requirements-dev.txt
# (requirements-dev.txt pulls in requirements.txt transitively)

# 4. Configure environment
cp .env.example .env
# edit .env: DATABASE_URL, SUPABASE_JWT_SECRET, FRONTEND_ORIGIN

# 5. Apply database migrations
alembic upgrade head

# 6. Install pre-commit hooks (both the pre-commit and commit-msg hooks)
pre-commit install --install-hooks
pre-commit install --hook-type commit-msg

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

The format is enforced by [`commitizen`](https://commitizen-tools.github.io/commitizen/) via a `commit-msg` pre-commit hook. `git commit` will reject messages that do not parse as Conventional Commits — local validation is identical to what CI sees.

Quick reference:

| Command | What it does |
|---|---|
| `cz commit` | Interactive commit-message builder. Walks you through type / scope / message / breaking-change flags. |
| `cz check --message "feat(api): add /healthz"` | Manually validate one message without committing. |
| `cz bump` | Bump the version in `pyproject.toml` based on commits since the last tag, create an annotated `vX.Y.Z` tag, and stage the change. Run this when cutting a release. |
| `cz bump --dry-run` | Preview the next version without changing anything. |

## PR checklist

Before opening a pull request:

- [ ] `pre-commit run --all-files` passes (black + flake8)
- [ ] `pytest` passes locally
- [ ] New endpoints have at least one happy-path test
- [ ] New models include an Alembic migration (`alembic revision --autogenerate -m "..."`)
- [ ] Public schema changes are reflected in `docs/API.md` or `docs/DATABASE.md`
- [ ] `[Unreleased]` section of `CHANGELOG.md` updated if user-visible

CI runs the same checks on every PR — see [`.github/workflows/`](.github/workflows/).

## Dependency management

Dependencies are managed with [pip-tools](https://github.com/jazzband/pip-tools):

| File | Role |
|---|---|
| `requirements.in` | Hand-edited list of direct production dependencies. |
| `requirements-dev.in` | Hand-edited list of direct dev-only dependencies (test, lint, lockfile tooling). Inherits from `requirements.txt`. |
| `requirements.txt` | **Generated.** Fully pinned, hash-locked production lockfile. Render reads this. |
| `requirements-dev.txt` | **Generated.** Fully pinned, hash-locked dev lockfile. Contributors and CI read this. |

To **add** or **update** a dependency:

```bash
# 1. Edit requirements.in or requirements-dev.in by hand.
# 2. Recompile both lockfiles:
pip-compile --generate-hashes --strip-extras requirements.in -o requirements.txt
pip-compile --generate-hashes --strip-extras --allow-unsafe requirements-dev.in -o requirements-dev.txt
# 3. Sync your local env to the new lockfile:
pip-sync requirements-dev.txt
# 4. Commit both .in and .txt files together.
```

To **upgrade everything** to the latest versions allowed by `.in`:

```bash
pip-compile --generate-hashes --strip-extras --upgrade requirements.in -o requirements.txt
pip-compile --generate-hashes --strip-extras --allow-unsafe --upgrade requirements-dev.in -o requirements-dev.txt
```

Render's build command runs plain `pip install -r requirements.txt` — no pip-tools required in production.

### Generating lockfiles on the right platform

`pip-compile` only sees the constraints visible to the Python interpreter it runs under. Running it on macOS Python 3.9 silently misses transitive deps that are Py3.10+-only on Linux (e.g. `filelock>=3.24.2` required by recent `virtualenv`, or `greenlet` pulled in by SQLAlchemy on Linux). The resulting lockfile then fails `--require-hashes` install on CI.

If your local Python is < 3.11, **do not regenerate the lockfile locally** — trigger the GitHub Actions workflow instead:

```bash
gh workflow run "Update lockfiles"   # runs pip-compile on Linux Py3.11 and opens a PR
```

The workflow lives at [`.github/workflows/lockfile.yml`](.github/workflows/lockfile.yml).

## Code style

- Code is formatted by **black** (config in `pyproject.toml`) and linted by **flake8** (config in `.pre-commit-config.yaml`).
- Both run automatically via pre-commit on staged files; configure your editor to run them on save for the best experience.
- Comments only when the **why** is non-obvious: a hidden constraint, a subtle invariant, or a workaround for a specific external bug.
- Do not add comments that reference the current task, PR, or issue number — those belong in the PR description and rot as the code evolves.
- Public functions should have a one-line docstring describing what they return, not how.

## Testing

See [`docs/TESTING.md`](docs/TESTING.md) for the full testing guide. Quick reference:

| Command | Purpose |
|---|---|
| `pytest` | Run the full suite (no coverage). |
| `pytest --cov=app` | Run with coverage; uses the threshold in `pyproject.toml`. |
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

To cut a release:

```bash
# 1. Make sure CHANGELOG.md's [Unreleased] section is up to date.

# 2. Preview which version commitizen would pick (major / minor / patch).
cz bump --dry-run

# 3. Apply the bump: updates pyproject.toml [project].version, commits the
#    bump, creates an annotated `vX.Y.Z` tag.
cz bump

# 4. Push the bump commit and tag.
git push --follow-tags

# 5. Create the GitHub Release from the matching CHANGELOG section.
gh release create vX.Y.Z --notes-file <(awk '/^## \[X\.Y\.Z\]/,/^## \[/' CHANGELOG.md)
```

The version `cz bump` picks is determined by the commits since the last tag: `fix:` → patch, `feat:` → minor, anything with `BREAKING CHANGE:` in the body or a `!` after the type → major.
