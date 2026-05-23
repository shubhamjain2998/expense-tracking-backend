"""HTTP routers — one module per API domain.

Each submodule exports a ``router: APIRouter`` that ``app.main`` wires into the
FastAPI app:

    auth                — /auth/{register,login,logout,me}
    admin               — /admin/* (development-time bulk wipes, per user)
    budget              — /budget — annual budget plans per category
    uploads             — /uploads/{statement,preview,text-import,preview-text}
    transactions        — /transactions/{raw,processed,auto-categorise,process,…}
    categories          — /categories
    category_mappings   — /category-mappings (learned description → category rules)
    tags                — /tags
    persons             — /persons (expense-split participants)
    dashboard           — /dashboard/* (summary, trend, ytd, split-ledger, …)
    backup              — /backup/{export,import}
"""
