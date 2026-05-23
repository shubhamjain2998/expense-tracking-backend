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
from fastapi.responses import JSONResponse
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


@app.get("/health")
def health():
    return {"status": "ok"}
