"""Backup must carry a rule whole, and must not sever restored rows from it.

Two regressions are pinned here:

1. ``category_mappings`` used to export only (pattern, category), so a restore
   silently dropped every rule's tags and split.
2. The restore set ``mapping_id=None`` on every processed row. On a real
   account that left 6 of 4010 rows linked to a rule — the link the UI and
   auto-categorise both read.
"""

import os
import uuid
from datetime import date

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SUPABASE_JWT_SECRET", "pytest-placeholder-secret")

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import CategoryMapping, ProcessedTransaction  # noqa: E402
from app.schemas import BackupImport  # noqa: E402
from app.services.backup import export_user_data, import_user_data  # noqa: E402

USER_ID = uuid.uuid4()


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SS = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    return SS()


PAYLOAD = {
    "version": "1",
    "categories": [{"name": "food"}],
    "tags": [{"name": "eating out"}],
    "persons": [{"name": "flatmate"}],
    "budget_plans": [],
    "category_mappings": [
        {
            "description_pattern": "swiggy order",
            "category": "food",
            "tags": ["eating out"],
            "shares": [
                {
                    "person": "flatmate",
                    "share_type": "percentage",
                    "share_value": "50",
                }
            ],
        }
    ],
    "transactions": [
        {
            "txn_date": "2026-05-10",
            "description": "swiggy order",
            "amount": "400.00",
            "category": "food",
            "tags": ["eating out"],
            "shares": [
                {
                    "person": "flatmate",
                    "share_type": "percentage",
                    "share_value": "50",
                    "settled": False,
                }
            ],
        }
    ],
}


def test_import_restores_rule_context(session):
    import_user_data(BackupImport(**PAYLOAD), USER_ID, session)

    mapping = session.query(CategoryMapping).one()
    assert [t.name for t in mapping.tags] == ["eating out"]
    assert len(mapping.shares) == 1
    assert mapping.shares[0].share_type == "percentage"


def test_import_relinks_transactions_to_their_rule(session):
    import_user_data(BackupImport(**PAYLOAD), USER_ID, session)

    mapping = session.query(CategoryMapping).one()
    txn = session.query(ProcessedTransaction).one()
    assert txn.mapping_id == mapping.id


def test_export_round_trips(session):
    import_user_data(BackupImport(**PAYLOAD), USER_ID, session)
    exported = export_user_data(USER_ID, session)

    assert len(exported.category_mappings) == 1
    m = exported.category_mappings[0]
    assert m.tags == ["eating out"]
    assert m.shares[0].person == "flatmate"
    assert m.shares[0].share_type == "percentage"

    # Feed the export back into a fresh account: identical rule.
    other = uuid.uuid4()
    import_user_data(BackupImport(**exported.model_dump(mode="json")), other, session)
    copied = (
        session.query(CategoryMapping).filter(CategoryMapping.user_id == other).one()
    )
    assert [t.name for t in copied.tags] == ["eating out"]
    assert len(copied.shares) == 1


def test_older_backup_without_rule_context_still_imports(session):
    """A file written before rules carried tags restores as a category-only
    rule rather than failing validation."""
    legacy = dict(PAYLOAD)
    legacy["category_mappings"] = [
        {"description_pattern": "swiggy order", "category": "food"}
    ]
    import_user_data(BackupImport(**legacy), USER_ID, session)

    mapping = session.query(CategoryMapping).one()
    assert mapping.tags == []
    assert mapping.shares == []


def test_derived_mappings_do_not_wipe_existing_rule_context(session):
    """When a payload omits category_mappings the rules are derived from the
    transactions, and say nothing about tags — so they must not clear them."""
    import_user_data(BackupImport(**PAYLOAD), USER_ID, session)

    second = dict(PAYLOAD)
    second.pop("category_mappings")
    second["transactions"] = [
        {
            "txn_date": "2026-06-10",
            "description": "swiggy order",
            "amount": "300.00",
            "category": "food",
        }
    ]
    import_user_data(BackupImport(**second), USER_ID, session)

    mapping = session.query(CategoryMapping).one()
    assert [t.name for t in mapping.tags] == ["eating out"]
    assert len(mapping.shares) == 1


def test_import_reports_unknown_rule_entities(session):
    payload = dict(PAYLOAD)
    payload["category_mappings"] = [
        {
            "description_pattern": "swiggy order",
            "category": "food",
            "tags": ["eating out"],
            "shares": [
                {
                    "person": "ghost",
                    "share_type": "percentage",
                    "share_value": "10",
                }
            ],
        }
    ]
    result = import_user_data(BackupImport(**payload), USER_ID, session)
    # 'ghost' is created as a person by the name-collection pass, so the rule
    # keeps its split rather than losing it silently.
    mapping = session.query(CategoryMapping).one()
    assert len(mapping.shares) == 1
    assert isinstance(result.skipped_rows, list)


def test_date_type_is_preserved(session):
    import_user_data(BackupImport(**PAYLOAD), USER_ID, session)
    txn = session.query(ProcessedTransaction).one()
    assert txn.txn_date == date(2026, 5, 10)
