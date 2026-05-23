# Deployment

The production target is **Render** for the API and **Supabase** for the database. Both have free tiers that this project fits inside; everything below describes that path.

## Environment variables

| Name | Required | Example | Notes |
|---|---|---|---|
| `DATABASE_URL` | yes | `postgresql://postgres.xxx:pwd@aws-0-region.pooler.supabase.com:6543/postgres` | Supabase **Connection Pooler** URL (port `6543`). Direct connection (port `5432`) only works for migrations. |
| `SUPABASE_JWT_SECRET` | yes | `super-secret-256-bit-value` | The shared secret used to sign JWTs. Found under Project Settings → API → JWT Settings in Supabase. |
| `FRONTEND_ORIGIN` | yes | `https://expense-tracking-frontend.vercel.app` | Single allowed CORS origin. Must **not** end in `/`. Wildcard `*` is forbidden because cookies require an explicit origin. |
| `COOKIE_SECURE` | default `false` | `true` | Set `true` in production (HTTPS only). Browsers reject `Secure` cookies over HTTP, so keep `false` for local dev. |
| `COOKIE_SAMESITE` | default `strict` | `lax` | `strict` blocks third-party requests entirely; `lax` allows top-level GET navigations. `none` requires `secure=true`. |

`.env.example` is the source of truth for the required keys.

## Supabase setup

1. Create a new project in [Supabase](https://supabase.com/). Note the project URL and the **database password** — you cannot see the password again.
2. Project Settings → Database → Connection Info gives you two strings:
   - **Direct connection** (port `5432`) — used for `alembic upgrade head` from your laptop.
   - **Connection pooler** (port `6543`, transaction mode) — used by the running app.
3. Project Settings → API → JWT Settings — copy the JWT secret. This becomes `SUPABASE_JWT_SECRET`.
4. Apply the schema:
   ```bash
   DATABASE_URL='postgresql://postgres:<password>@db.<project>.supabase.co:5432/postgres' \
     alembic upgrade head
   ```

## Render setup

Create a new **Web Service** pointing at this repository:

| Setting | Value |
|---|---|
| Environment | `Python 3` |
| Region | Same as your Supabase project (lower latency, cheaper egress). |
| Branch | `main` |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health check path | `/health` |
| Auto-deploy | On (deploy on every push to `main`). |

Add all environment variables from the table above to **Environment** → **Environment Variables**. Use the connection-pooler URL (`6543`) for `DATABASE_URL`, not the direct connection.

## First deploy checklist

- [ ] Supabase project created, password stored.
- [ ] Schema applied locally against the direct connection (`alembic upgrade head`).
- [ ] Render service created and env vars set.
- [ ] First push to `main` triggers a build → `/health` returns `200`.
- [ ] Update the frontend's `VITE_API_BASE_URL` to the Render URL.
- [ ] Update `FRONTEND_ORIGIN` on Render to the deployed frontend URL.
- [ ] Optional: tighten `COOKIE_SECURE=true` and `COOKIE_SAMESITE=strict` once both are on HTTPS.

## Applying migrations to production

Migrations are **not** run by the Render build. Apply them from your laptop pointed at the direct-connection URL:

```bash
DATABASE_URL='postgresql://postgres:<password>@db.<project>.supabase.co:5432/postgres' \
  alembic upgrade head
```

This keeps migrations a deliberate, reviewed action separate from a deploy. If you'd rather automate it, add `alembic upgrade head &&` in front of the start command — be aware that any failed migration will then crash the service.

## Rolling back

Render keeps each successful build; clicking **Rollback** on a prior deploy returns the API binary to that point. Migrations are **not** rolled back automatically — if a deploy added a migration that needs reverting, run `alembic downgrade -1` manually before rolling back the code.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `psycopg2.OperationalError: server closed the connection unexpectedly` shortly after deploy. | Using the direct-connection URL (`5432`) on Render. Supabase closes idle direct connections — use the pooler URL (`6543`). |
| CORS error in the browser even though `FRONTEND_ORIGIN` is set. | `FRONTEND_ORIGIN` has a trailing `/`. Strip it. |
| `401` on every request after login works fine. | `COOKIE_SECURE=true` but the frontend talks to a non-HTTPS backend; cookie was set but the browser refuses to send it back. |
| Render build timeout. | Some wheels (`psycopg2-binary`, `pandas`) take a while; bump the build timeout in Render settings or pin slimmer alternatives. |
| `/health` returns 200 but every authenticated endpoint returns 500. | `SUPABASE_JWT_SECRET` is wrong; tokens decode in dev but not prod. |

## Local production-like run

To preview what Render will run, locally:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
# (without --reload, which Render does not use)
```

## Related documents

- [`DEVELOPMENT.md`](DEVELOPMENT.md) — local-dev walkthrough
- [`DATABASE.md`](DATABASE.md) — migrations workflow in more detail
