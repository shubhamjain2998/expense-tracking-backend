# Development

A walkthrough for getting the backend running locally and making changes.

## Prerequisites

- Python **3.11 or newer**
- PostgreSQL 14+ running locally, or a Supabase project for which you have the direct-connection URL
- Git
- A POSIX-y shell (commands below assume bash / zsh; PowerShell users should swap `source venv/bin/activate` for `.\venv\Scripts\activate`)

## First-time setup

```bash
# 1. Clone
git clone git@github.com:shubhamjain2998/expense-tracking-backend.git
cd expense-tracking-backend

# 2. Virtual environment
python -m venv venv
source venv/bin/activate

# 3. Dependencies (production + dev tooling)
pip install -r requirements-dev.txt
# (requirements-dev.txt pulls in the production lockfile transitively)

# 4. Environment file
cp .env.example .env
# Edit .env:
#   DATABASE_URL          local Postgres or Supabase direct URL
#   SUPABASE_JWT_SECRET   any 32-byte random string for local dev
#   FRONTEND_ORIGIN       http://localhost:5173 (Vite default)

# 5. Database
createdb expense_tracker          # if using local Postgres
alembic upgrade head

# 6. Pre-commit hooks
pre-commit install

# 7. Run
python server.py
# → http://localhost:8000
# → http://localhost:8000/docs    (Swagger)
# → http://localhost:8000/redoc   (ReDoc)
```

## Daily workflow

```bash
source venv/bin/activate
git pull
pip-sync requirements-dev.txt      # only when either lockfile changed
alembic upgrade head               # only when new migrations landed
python server.py
```

The dev server runs with `--reload`, so any `.py` change restarts the process within ~1 second.

## How to …

### Add an endpoint

1. Pick (or create) a router under `app/routers/`.
2. Define request / response models in `app/schemas.py`.
3. Implement the function — declare `db: Session = Depends(get_db)` and `user_id: uuid.UUID = Depends(get_current_user)` for any authenticated route.
4. Add the router to `app.include_router(...)` in `app/main.py` if it is brand new.
5. Add at least one happy-path test under `tests/`.

```python
@router.post("/example", response_model=ExampleOut, status_code=201)
def create_example(
    body: ExampleCreate,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    row = Example(user_id=user_id, **body.dict())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
```

### Add a model

1. Append the class to `app/models.py`, inheriting `TimestampMixin` and `Base`. Always include `user_id: Mapped[uuid.UUID]`.
2. Add a unique constraint for `(user_id, <natural key>)` if applicable.
3. Generate a migration: `alembic revision --autogenerate -m "add <table>"`.
4. **Review the migration file** under `alembic/versions/` — autogenerate is approximate, especially for indexes and enums.
5. `alembic upgrade head` to apply locally.
6. Update [`docs/DATABASE.md`](DATABASE.md) — both the Mermaid ERD and the Tables section.

### Add a migration without a model change

```bash
alembic revision -m "backfill description casefold"
# Edit the new file's upgrade() / downgrade() by hand.
alembic upgrade head
```

A real example lives at [`scripts/backfill_clean_descriptions.py`](../scripts/backfill_clean_descriptions.py) — backfills can be either a migration or a one-off script; prefer migrations when the change must run on every environment.

### Edit a schema

`app/schemas.py` holds every Pydantic model. Group changes by feature: request DTOs above their response DTOs. Keep field names aligned with the SQLAlchemy model where possible — the frontend renames them only at the edge.

### Add a dependency

Dependencies are managed with [pip-tools](https://github.com/jazzband/pip-tools). Never edit `requirements*.txt` by hand — they are generated.

```bash
# 1. Add the new line to requirements.in (production) or requirements-dev.in (dev only).
echo "rich" >> requirements.in   # version-pin only if you have a reason

# 2. Recompile the lockfile(s).
pip-compile --generate-hashes --strip-extras requirements.in -o requirements.txt
pip-compile --generate-hashes --strip-extras --allow-unsafe requirements-dev.in -o requirements-dev.txt

# 3. Sync your local env.
pip-sync requirements-dev.txt

# 4. Commit both the .in and .txt changes together.
```

`pip-sync` will install missing packages **and** remove anything not in the lockfile — so it leaves you with exactly the set CI will see.

### Reset the local database

```bash
./drop_db.sh         # warning: nukes the local DB
alembic upgrade head
```

## Code style

- **black** formats all `.py` files (88-char line length).
- **flake8** lints with `--max-line-length=88 --extend-ignore=E203,W503` (matches black).
- Both run on staged files via pre-commit on `git commit`. If you skip the hook (`git commit -n`), CI will reject the PR.

Editor setup:

- VS Code — install the official Python and "Black Formatter" extensions; `"editor.formatOnSave": true` in `.vscode/settings.json`.
- PyCharm — Settings → Tools → File Watchers → add `black` watching `*.py`.

## Running against the frontend

```
# Terminal A
cd ../frontend
npm run dev                 # http://localhost:5173

# Terminal B
cd ../backend
python server.py            # http://localhost:8000
```

Make sure `FRONTEND_ORIGIN=http://localhost:5173` in `.env` and the frontend's `VITE_API_BASE_URL=http://localhost:8000`. The auth cookie's `SameSite=strict` works for this same-site pair (different ports on `localhost` count as same-site).

## Related documents

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — what each module owns
- [`TESTING.md`](TESTING.md) — pytest layout and conventions
- [`DEPLOYMENT.md`](DEPLOYMENT.md) — production setup
