from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import (
    admin,
    budget,
    uploads,
    transactions,
    categories,
    persons,
    dashboard,
)

app = FastAPI(title="Expense Tracker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "Accept",
        "Origin",
        "X-Requested-With",
    ],
)

app.include_router(admin.router)
app.include_router(budget.router)
app.include_router(uploads.router)
app.include_router(transactions.router)
app.include_router(categories.router)
app.include_router(persons.router)
app.include_router(dashboard.router)


@app.get("/health")
def health():
    return {"status": "ok"}
