"""Expense Tracker backend — FastAPI application package.

Submodules:
    main      — FastAPI app, CORS, exception handler, router wiring
    config    — pydantic-settings reading environment variables
    database  — SQLAlchemy engine, sessionmaker, ``get_db`` dependency
    auth      — ``get_current_user`` dependency (cookie or Bearer JWT)
    models    — all SQLAlchemy table definitions
    schemas   — all Pydantic request / response models
    routers   — one module per API domain (auth, budget, transactions, …)
    services  — PDF / text parsing, description normalisation, period helpers

See ``docs/ARCHITECTURE.md`` for the bigger picture.
"""
