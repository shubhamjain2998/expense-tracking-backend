"""Export and import a user's full data set as a single JSON document.

The export emits everything user-owned: categories, tags, persons, budget plans,
category mappings, and processed transactions (with their tags + person shares).
The import is the inverse, plus an Excel-friendly mode where omitting
``category_mappings`` causes them to be derived from the (description, category)
pairs in the imported transactions.

Entities are referenced by name (not UUID) so a backup is portable across user
accounts and so spreadsheet-derived JSON can be hand-authored.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    BudgetPlan,
    Category,
    CategoryMapping,
    Person,
    ProcessedTransaction,
    RawTransaction,
    Tag,
    TransactionPersonShare,
)
from app.schemas import (
    BackupBudgetPlan,
    BackupCategoryMapping,
    BackupExport,
    BackupImport,
    BackupImportResponse,
    BackupNamedEntity,
    BackupShare,
    BackupTransaction,
)

BACKUP_FORMAT_VERSION = "1"


# ─── Export ───────────────────────────────────────────────────────────────────


def export_user_data(user_id: uuid.UUID, db: Session) -> BackupExport:
    categories = (
        db.execute(
            select(Category).where(Category.user_id == user_id).order_by(Category.name)
        )
        .scalars()
        .all()
    )
    tags = (
        db.execute(select(Tag).where(Tag.user_id == user_id).order_by(Tag.name))
        .scalars()
        .all()
    )
    persons = (
        db.execute(
            select(Person).where(Person.user_id == user_id).order_by(Person.name)
        )
        .scalars()
        .all()
    )
    budget_plans = (
        db.execute(
            select(BudgetPlan)
            .where(BudgetPlan.user_id == user_id)
            .options(selectinload(BudgetPlan.category))
            .order_by(BudgetPlan.year)
        )
        .scalars()
        .all()
    )
    mappings = (
        db.execute(
            select(CategoryMapping)
            .where(CategoryMapping.user_id == user_id)
            .options(selectinload(CategoryMapping.category))
            .order_by(CategoryMapping.description_pattern)
        )
        .scalars()
        .all()
    )
    txns = (
        db.execute(
            select(ProcessedTransaction)
            .where(ProcessedTransaction.user_id == user_id)
            .options(
                selectinload(ProcessedTransaction.category),
                selectinload(ProcessedTransaction.tags),
                selectinload(ProcessedTransaction.shares).selectinload(
                    TransactionPersonShare.person
                ),
            )
            .order_by(ProcessedTransaction.txn_date, ProcessedTransaction.description)
        )
        .scalars()
        .all()
    )

    return BackupExport(
        version=BACKUP_FORMAT_VERSION,
        exported_at=datetime.now(timezone.utc),
        categories=[BackupNamedEntity(name=c.name) for c in categories],
        tags=[BackupNamedEntity(name=t.name) for t in tags],
        persons=[BackupNamedEntity(name=p.name) for p in persons],
        budget_plans=[
            BackupBudgetPlan(
                year=bp.year,
                category=bp.category.name,
                allocated_amount=Decimal(str(bp.allocated_amount)),
            )
            for bp in budget_plans
        ],
        category_mappings=[
            BackupCategoryMapping(
                description_pattern=m.description_pattern,
                category=m.category.name,
            )
            for m in mappings
        ],
        transactions=[
            BackupTransaction(
                txn_date=t.txn_date,
                description=t.description,
                amount=Decimal(str(t.amount)),
                category=t.category.name,
                notes=t.notes,
                tags=[tag.name for tag in t.tags],
                shares=[
                    BackupShare(
                        person=s.person.name,
                        share_type=s.share_type,
                        share_value=Decimal(str(s.share_value)),
                        settled=s.settled,
                    )
                    for s in t.shares
                ],
            )
            for t in txns
        ],
    )


# ─── Import ───────────────────────────────────────────────────────────────────


def _norm(name: str) -> str:
    return name.strip().lower()


def _upsert_categories(
    names: List[str], user_id: uuid.UUID, db: Session
) -> Tuple[Dict[str, Category], int]:
    """Returns (name → Category, count of newly created)."""
    existing = (
        db.execute(select(Category).where(Category.user_id == user_id)).scalars().all()
    )
    by_name: Dict[str, Category] = {c.name: c for c in existing}
    created = 0
    for raw in names:
        n = _norm(raw)
        if not n or n in by_name:
            continue
        cat = Category(user_id=user_id, name=n)
        db.add(cat)
        db.flush()
        by_name[n] = cat
        created += 1
    return by_name, created


def _upsert_tags(
    names: List[str], user_id: uuid.UUID, db: Session
) -> Tuple[Dict[str, Tag], int]:
    existing = db.execute(select(Tag).where(Tag.user_id == user_id)).scalars().all()
    by_name: Dict[str, Tag] = {t.name: t for t in existing}
    created = 0
    for raw in names:
        n = _norm(raw)
        if not n or n in by_name:
            continue
        tag = Tag(user_id=user_id, name=n)
        db.add(tag)
        db.flush()
        by_name[n] = tag
        created += 1
    return by_name, created


def _upsert_persons(
    names: List[str], user_id: uuid.UUID, db: Session
) -> Tuple[Dict[str, Person], int]:
    existing = (
        db.execute(select(Person).where(Person.user_id == user_id)).scalars().all()
    )
    by_name: Dict[str, Person] = {p.name: p for p in existing}
    created = 0
    for raw in names:
        n = _norm(raw)
        if not n or n in by_name:
            continue
        p = Person(user_id=user_id, name=n)
        db.add(p)
        db.flush()
        by_name[n] = p
        created += 1
    return by_name, created


def _compute_share_amount(total: Decimal, share: BackupShare) -> Decimal:
    if share.share_type == "percentage":
        return total * share.share_value / Decimal("100")
    return share.share_value


def import_user_data(
    payload: BackupImport, user_id: uuid.UUID, db: Session
) -> BackupImportResponse:
    skipped_rows: List[str] = []

    # 1. Collect every entity name referenced anywhere — top-level lists + transactions.
    cat_names = {c.name for c in payload.categories}
    cat_names.update(t.category for t in payload.transactions)
    cat_names.update(bp.category for bp in payload.budget_plans)
    if payload.category_mappings is not None:
        cat_names.update(m.category for m in payload.category_mappings)

    tag_names = {t.name for t in payload.tags}
    for t in payload.transactions:
        tag_names.update(t.tags)

    person_names = {p.name for p in payload.persons}
    for t in payload.transactions:
        person_names.update(s.person for s in t.shares)

    # 2. Upsert reference entities.
    cats_by_name, cats_created = _upsert_categories(list(cat_names), user_id, db)
    tags_by_name, tags_created = _upsert_tags(list(tag_names), user_id, db)
    persons_by_name, persons_created = _upsert_persons(list(person_names), user_id, db)

    # 3. Budget plans (year + category is the natural key; replaces existing).
    budget_created = 0
    for bp in payload.budget_plans:
        cat = cats_by_name[_norm(bp.category)]
        existing = db.execute(
            select(BudgetPlan).where(
                BudgetPlan.user_id == user_id,
                BudgetPlan.year == bp.year,
                BudgetPlan.category_id == cat.id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.allocated_amount = float(bp.allocated_amount)
        else:
            db.add(
                BudgetPlan(
                    user_id=user_id,
                    year=bp.year,
                    category_id=cat.id,
                    allocated_amount=float(bp.allocated_amount),
                )
            )
            budget_created += 1
    db.flush()

    # 4. Pre-load existing processed transactions for duplicate detection.
    existing_txn_keys = set(
        db.execute(
            select(
                ProcessedTransaction.txn_date,
                ProcessedTransaction.description,
                ProcessedTransaction.amount,
            ).where(ProcessedTransaction.user_id == user_id)
        ).all()
    )

    # 5. Insert transactions (raw + processed + shares + tag links).
    txns_imported = 0
    txns_skipped = 0
    derived_mappings: Dict[str, str] = (
        {}
    )  # description → category (used if mappings omitted)

    for idx, t in enumerate(payload.transactions):
        cat_key = _norm(t.category)
        if cat_key not in cats_by_name:
            skipped_rows.append(f"transactions[{idx}]: unknown category '{t.category}'")
            continue

        key = (t.txn_date, t.description, Decimal(str(t.amount)))
        if key in existing_txn_keys:
            txns_skipped += 1
            continue

        cat = cats_by_name[cat_key]
        amount_dec = Decimal(str(t.amount))
        txn_dt = datetime.combine(t.txn_date, datetime.min.time())

        raw = RawTransaction(
            user_id=user_id,
            txn_date=txn_dt,
            description=t.description,
            amount=float(amount_dec),
            status="processed",
        )
        db.add(raw)
        db.flush()

        # Compute shares + effective amount
        others_total = Decimal("0")
        share_records: List[TransactionPersonShare] = []
        skip_txn = False
        for s_idx, s in enumerate(t.shares):
            person_key = _norm(s.person)
            if person_key not in persons_by_name:
                skipped_rows.append(
                    f"transactions[{idx}].shares[{s_idx}]: unknown person '{s.person}'"
                )
                skip_txn = True
                break
            share_amt = _compute_share_amount(amount_dec, s)
            others_total += share_amt
            share_records.append(
                TransactionPersonShare(
                    person_id=persons_by_name[person_key].id,
                    share_type=s.share_type,
                    share_value=float(s.share_value),
                    share_amount=float(share_amt),
                    settled=s.settled,
                )
            )

        if skip_txn:
            db.delete(raw)
            db.flush()
            continue

        effective_amount = amount_dec - others_total

        processed = ProcessedTransaction(
            user_id=user_id,
            raw_txn_id=raw.id,
            mapping_id=None,
            category_id=cat.id,
            txn_date=t.txn_date,
            description=t.description,
            amount=float(amount_dec),
            effective_amount=float(effective_amount),
            month=t.txn_date.month,
            year=t.txn_date.year,
            notes=t.notes,
        )
        db.add(processed)
        db.flush()

        for rec in share_records:
            rec.processed_txn_id = processed.id
            db.add(rec)

        # Tags
        if t.tags:
            tag_objs = []
            for tag_name in t.tags:
                tag_key = _norm(tag_name)
                if tag_key in tags_by_name:
                    tag_objs.append(tags_by_name[tag_key])
            processed.tags = tag_objs

        existing_txn_keys.add(key)
        txns_imported += 1

        # Track for derived mappings (last write wins)
        derived_mappings[t.description.strip()] = cat_key

    # 6. Category mappings: explicit list wins; otherwise derived from transactions.
    mappings_to_create: List[BackupCategoryMapping]
    if payload.category_mappings is not None:
        mappings_to_create = payload.category_mappings
    else:
        mappings_to_create = [
            BackupCategoryMapping(description_pattern=desc, category=cat_name)
            for desc, cat_name in derived_mappings.items()
        ]

    existing_patterns = {
        m.description_pattern: m
        for m in db.execute(
            select(CategoryMapping).where(CategoryMapping.user_id == user_id)
        )
        .scalars()
        .all()
    }
    mappings_created = 0
    for m in mappings_to_create:
        cat_key = _norm(m.category)
        if cat_key not in cats_by_name:
            skipped_rows.append(
                f"category_mappings: unknown category '{m.category}' "
                f"for pattern '{m.description_pattern}'"
            )
            continue
        pattern = m.description_pattern.strip()
        if not pattern:
            continue
        cat = cats_by_name[cat_key]
        if pattern in existing_patterns:
            existing_patterns[pattern].category_id = cat.id
        else:
            new_mapping = CategoryMapping(
                user_id=user_id,
                description_pattern=pattern,
                category_id=cat.id,
                match_count=0,
                last_used=None,
            )
            db.add(new_mapping)
            db.flush()
            existing_patterns[pattern] = new_mapping
            mappings_created += 1

    db.commit()

    return BackupImportResponse(
        categories_created=cats_created,
        tags_created=tags_created,
        persons_created=persons_created,
        budget_plans_created=budget_created,
        category_mappings_created=mappings_created,
        transactions_imported=txns_imported,
        transactions_skipped_duplicates=txns_skipped,
        skipped_rows=skipped_rows,
    )
