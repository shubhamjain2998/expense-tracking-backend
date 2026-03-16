from fastapi import FastAPI
from app.routers import budget, uploads, transactions, categories, persons, dashboard

app = FastAPI(title="Expense Tracker API")

app.include_router(budget.router)
app.include_router(uploads.router)
app.include_router(transactions.router)
app.include_router(categories.router)
app.include_router(persons.router)
app.include_router(dashboard.router)


@app.get("/health")
def health():
    return {"status": "ok"}
