"""FastAPI application entry point.

Wires every router into a single ``app`` instance, configures CORS for the
single ``FRONTEND_ORIGIN`` (wildcard is forbidden when ``allow_credentials`` is
true), and installs a catch-all exception handler so CORS headers attach even
on 500 responses — without it, Starlette's ``ServerErrorMiddleware`` returns a
bare 500 and the browser surfaces a misleading "blocked by CORS policy" error.

Run locally with ``python server.py`` (uvicorn + reload) or
``uvicorn app.main:app --reload`` directly.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from app.config import settings
from app.routers import (
    admin,
    auth,
    backup,
    budget,
    uploads,
    transactions,
    categories,
    category_mappings,
    persons,
    dashboard,
    tags,
)

logger = logging.getLogger("app")

app = FastAPI(title="Expense Tracker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "Accept",
        "Origin",
        "X-Requested-With",
    ],
)


# Catch-all exception handler. Without this, Starlette's ServerErrorMiddleware
# sits OUTSIDE the CORS middleware and returns a 500 with no CORS headers, so
# the browser surfaces "blocked by CORS policy" instead of the real error.
# Routing the response through a FastAPI handler ensures CORS headers attach.
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(budget.router)
app.include_router(uploads.router)
app.include_router(transactions.router)
app.include_router(categories.router)
app.include_router(category_mappings.router)
app.include_router(persons.router)
app.include_router(dashboard.router)
app.include_router(tags.router)
app.include_router(backup.router)


_LANDING_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Expense Tracker API</title>
<style>
  :root {
    --bg: #0a0a0b;
    --surface: rgba(255,255,255,0.04);
    --border: rgba(255,255,255,0.08);
    --text: #e8e8ea;
    --muted: #7c7c80;
    --accent: #a78bfa;
    --ok: #4ade80;
  }
  *, *::before, *::after { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; height: 100%; }
  body {
    font-family:
      -apple-system, BlinkMacSystemFont, "Segoe UI",
      Roboto, "Helvetica Neue", sans-serif;
    background:
      radial-gradient(1200px 600px at 15% -10%,
        rgba(167,139,250,0.20), transparent 60%),
      radial-gradient(900px 500px at 110% 110%,
        rgba(74,222,128,0.10), transparent 55%),
      var(--bg);
    color: var(--text);
    min-height: 100%;
    display: grid;
    place-items: center;
    padding: 24px;
    -webkit-font-smoothing: antialiased;
  }
  .card {
    width: min(560px, 100%);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 36px 36px 28px;
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    box-shadow:
      0 1px 0 rgba(255,255,255,0.05) inset,
      0 24px 60px -20px rgba(0,0,0,0.55);
  }
  .eyebrow {
    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
    font-size: 11px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--muted);
    margin: 0 0 18px;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--ok);
    box-shadow: 0 0 0 0 rgba(74,222,128,0.55);
    animation: pulse 2.2s ease-out infinite;
  }
  @keyframes pulse {
    0%   { box-shadow: 0 0 0 0   rgba(74,222,128,0.55); }
    70%  { box-shadow: 0 0 0 10px rgba(74,222,128,0); }
    100% { box-shadow: 0 0 0 0   rgba(74,222,128,0); }
  }
  h1 {
    font-size: 28px;
    font-weight: 620;
    letter-spacing: -0.012em;
    margin: 0 0 10px;
  }
  p.lede {
    color: var(--muted);
    margin: 0 0 26px;
    font-size: 15px;
    line-height: 1.55;
  }
  .stats {
    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
    font-size: 12.5px;
    background: rgba(255,255,255,0.025);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px 18px;
    margin: 0 0 22px;
    color: var(--muted);
    line-height: 1.95;
    white-space: pre;
    overflow-x: auto;
  }
  .stats b { color: var(--text); font-weight: 500; }
  .actions { display: flex; gap: 10px; flex-wrap: wrap; }
  a.btn {
    flex: 1 1 0;
    min-width: 140px;
    text-decoration: none;
    color: var(--text);
    background: rgba(255,255,255,0.04);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 16px;
    font-size: 14px;
    font-weight: 500;
    display: inline-flex;
    align-items: center;
    justify-content: space-between;
    transition: background 0.18s ease, border-color 0.18s ease;
  }
  a.btn:hover {
    background: rgba(255,255,255,0.07);
    border-color: rgba(255,255,255,0.18);
  }
  a.btn.primary {
    background: linear-gradient(180deg, rgba(167,139,250,0.20), rgba(167,139,250,0.08));
    border-color: rgba(167,139,250,0.38);
  }
  a.btn.primary:hover {
    background: linear-gradient(180deg, rgba(167,139,250,0.28), rgba(167,139,250,0.12));
  }
  .arrow { opacity: 0.6; transition: transform 0.18s ease, opacity 0.18s ease; }
  a.btn:hover .arrow { transform: translateX(2px); opacity: 1; }
  footer {
    margin-top: 22px;
    font-family: ui-monospace, monospace;
    font-size: 11px;
    color: var(--muted);
    text-align: center;
    letter-spacing: 0.05em;
  }
</style>
</head>
<body>
  <main class="card">
    <p class="eyebrow"><span class="dot"></span> operational</p>
    <h1>Expense Tracker API</h1>
    <p class="lede">
      FastAPI service powering a personal finance tracker —
      PDF ingestion, fuzzy categorisation, budgets, and analytics.
    </p>
    <pre class="stats">service  <b>expense-tracker-api</b>
runtime  <b>python 3.12 &middot; fastapi</b>
region   <b>ap-southeast-1 (singapore)</b>
docs     <b>/docs</b> &middot; interactive openapi</pre>
    <div class="actions">
      <a class="btn primary" href="__FRONTEND_URL__">
        <span>Open the app</span><span class="arrow">&rarr;</span>
      </a>
      <a class="btn" href="/docs">
        <span>API docs</span><span class="arrow">&rarr;</span>
      </a>
    </div>
    <footer>built by shubham</footer>
  </main>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root() -> HTMLResponse:
    return HTMLResponse(
        _LANDING_HTML.replace("__FRONTEND_URL__", settings.frontend_origin)
    )


@app.get("/health")
def health():
    return {"status": "ok"}
