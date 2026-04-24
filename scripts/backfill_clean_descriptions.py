"""
One-time backfill: apply clean_description() to all existing raw_transactions
and processed_transactions that still have noisy descriptions.

Run from the backend root:
    python scripts/backfill_clean_descriptions.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal  # noqa: E402
from app.models import ProcessedTransaction, RawTransaction  # noqa: E402
from app.services.normalizer import clean_description  # noqa: E402
from sqlalchemy import select  # noqa: E402


def backfill() -> None:
    db = SessionLocal()
    try:
        raw_txns = db.execute(select(RawTransaction)).scalars().all()
        raw_updated = 0
        for txn in raw_txns:
            cleaned = clean_description(txn.description)
            if cleaned != txn.description:
                txn.description = cleaned
                raw_updated += 1

        processed_txns = db.execute(select(ProcessedTransaction)).scalars().all()
        proc_updated = 0
        for txn in processed_txns:
            cleaned = clean_description(txn.description)
            if cleaned != txn.description:
                txn.description = cleaned
                proc_updated += 1

        db.commit()
        print(f"Done. raw={raw_updated} updated, processed={proc_updated} updated.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    backfill()
