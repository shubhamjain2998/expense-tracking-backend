"""Seed a demo user with 6 months of realistic Indian-context data.

Usage:
    DATABASE_URL=postgresql://... venv/bin/python scripts/seed_demo.py

The script is idempotent at the user level: if the demo user already exists,
it aborts with a hint to delete the user first (use `--reset` to wipe and
re-seed).
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import bcrypt
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

# Allow importing the FastAPI app modules when run from the backend dir.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import (  # noqa: E402
    BudgetPlan,
    Category,
    Person,
    ProcessedTransaction,
    RawTransaction,
    Tag,
    TransactionPersonShare,
    User,
    transaction_tags,
)

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "Demo1234!"

# Deterministic across runs.
random.seed(42)

# ─── Catalogue ────────────────────────────────────────────────────────────────

CATEGORIES = [
    "Rent",
    "Groceries",
    "Food & Dining",
    "Transport",
    "Utilities",
    "Subscriptions",
    "Shopping",
    "Health",
    "Entertainment",
    "Travel",
    "Education",
    "Personal Care",
    "Salary",
    "Freelance",
    "Investment Income",
]

PERSONS = ["Aarav", "Priya"]

TAGS = ["Work", "Personal", "Family", "Reimbursable", "Vacation"]

# Merchant catalogue per category — (description, amount_range, weight).
# Amounts are positive INR — sign convention is applied later from txn_type.
MERCHANTS: dict[str, list[tuple[str, tuple[int, int]]]] = {
    "Groceries": [
        ("Blinkit", (180, 1200)),
        ("BigBasket", (650, 3800)),
        ("Zepto", (210, 980)),
        ("Nature's Basket", (520, 2700)),
        ("Reliance Fresh", (420, 1850)),
    ],
    "Food & Dining": [
        ("Swiggy", (180, 920)),
        ("Zomato", (220, 1100)),
        ("Toit Brewpub", (1400, 3600)),
        ("Truffles", (650, 1800)),
        ("Glen's Bakehouse", (380, 1200)),
        ("Third Wave Coffee", (220, 580)),
        ("Starbucks", (320, 760)),
    ],
    "Transport": [
        ("Uber", (110, 540)),
        ("Ola", (95, 480)),
        ("Namma Metro", (40, 90)),
        ("BMTC Bus", (25, 65)),
        ("Indian Oil Fuel", (1200, 3400)),
        ("Rapido", (60, 240)),
    ],
    "Utilities": [
        ("BESCOM Electricity", (1100, 2800)),
        ("BWSSB Water", (380, 720)),
        ("Airtel Postpaid", (599, 999)),
        ("ACT Fibernet", (1099, 1499)),
        ("Indane Gas", (820, 1100)),
    ],
    "Subscriptions": [
        ("Netflix", (199, 649)),
        ("Spotify Premium", (119, 179)),
        ("Disney+ Hotstar", (149, 299)),
        ("Apple iCloud", (75, 219)),
        ("YouTube Premium", (129, 189)),
        ("Notion", (799, 799)),
    ],
    "Shopping": [
        ("Amazon", (340, 4500)),
        ("Myntra", (890, 3200)),
        ("Flipkart", (450, 2800)),
        ("Decathlon", (1200, 5500)),
        ("IKEA", (1800, 7800)),
        ("Lifestyle Stores", (1500, 4200)),
    ],
    "Health": [
        ("1mg Pharmacy", (220, 1400)),
        ("Apollo Pharmacy", (180, 1100)),
        ("Practo Consultation", (499, 1200)),
        ("Cult.fit", (1499, 2299)),
    ],
    "Entertainment": [
        ("BookMyShow PVR", (380, 1200)),
        ("INOX Garuda Mall", (320, 980)),
        ("Phoenix Marketcity", (450, 1400)),
    ],
    "Travel": [
        ("IRCTC Rail", (1100, 4800)),
        ("IndiGo Airlines", (4200, 12500)),
        ("Airbnb Goa", (5800, 18000)),
        ("MakeMyTrip Hotel", (3400, 14000)),
    ],
    "Education": [
        ("Udemy Course", (399, 2999)),
        ("Coursera", (4100, 4100)),
        ("Kindle eBook", (199, 599)),
    ],
    "Personal Care": [
        ("Lakme Salon", (1200, 3800)),
        ("Urbanclap Spa", (1499, 4500)),
        ("Nykaa", (480, 2400)),
    ],
}

# Negative-sign categories — income.
INCOME_MERCHANTS: dict[str, list[tuple[str, tuple[int, int]]]] = {
    "Salary": [("Acme Corp Payroll", (118000, 128000))],
    "Freelance": [
        ("Stripe Payout", (18000, 42000)),
        ("Razorpay Client X", (12000, 35000)),
    ],
    "Investment Income": [
        ("Zerodha Dividend", (800, 4500)),
        ("Groww Mutual Fund Redemption", (5000, 22000)),
    ],
}

# Fixed monthly anchors (description, category, amount, day-of-month).
MONTHLY_FIXED = [
    ("Acme Corp Payroll", "Salary", 122500, 1),
    ("Apartment Rent — Indiranagar", "Rent", 38500, 5),  # split with Aarav
    ("BESCOM Electricity", "Utilities", 1850, 10),
    ("BWSSB Water", "Utilities", 540, 10),
    ("Airtel Postpaid", "Utilities", 749, 14),
    ("ACT Fibernet", "Utilities", 1399, 14),
    ("Netflix", "Subscriptions", 649, 18),
    ("Spotify Premium", "Subscriptions", 179, 18),
    ("Disney+ Hotstar", "Subscriptions", 299, 20),
    ("Apple iCloud", "Subscriptions", 219, 22),
]

# Annual budget for 2026 (allocated_amount = full-year INR).
YEARLY_BUDGET = {
    "Rent": 38500 * 12,
    "Groceries": 16000 * 12,
    "Food & Dining": 12000 * 12,
    "Transport": 8000 * 12,
    "Utilities": 4500 * 12,
    "Subscriptions": 2400 * 12,
    "Shopping": 10000 * 12,
    "Health": 4000 * 12,
    "Entertainment": 3000 * 12,
    "Travel": 8000 * 12,
    "Education": 2500 * 12,
    "Personal Care": 3500 * 12,
}


# ─── Helpers ──────────────────────────────────────────────────────────────────


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def months_back(n: int, anchor: date) -> list[tuple[int, int]]:
    """Return (year, month) tuples for the last n months ending at anchor."""
    out: list[tuple[int, int]] = []
    y, m = anchor.year, anchor.month
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(out))


def random_day_in_month(year: int, month: int, today: date) -> date:
    """Random day within the month, but never in the future."""
    import calendar

    last = calendar.monthrange(year, month)[1]
    if year == today.year and month == today.month:
        last = min(last, today.day)
    return date(year, month, random.randint(1, last))


def pick_merchant(cat: str) -> tuple[str, Decimal]:
    pool = MERCHANTS[cat]
    name, (lo, hi) = random.choice(pool)
    amt = Decimal(random.randint(lo, hi))
    return name, amt


def pick_income(cat: str) -> tuple[str, Decimal]:
    pool = INCOME_MERCHANTS[cat]
    name, (lo, hi) = random.choice(pool)
    amt = Decimal(random.randint(lo, hi))
    return name, amt


# ─── Seeders ──────────────────────────────────────────────────────────────────


def wipe_user(db: Session, user_id: uuid.UUID) -> None:
    """Delete every row owned by the demo user. Order matters because of FKs."""
    proc_ids = [
        r[0]
        for r in db.execute(
            select(ProcessedTransaction.id).where(
                ProcessedTransaction.user_id == user_id
            )
        ).all()
    ]
    if proc_ids:
        db.execute(
            transaction_tags.delete().where(
                transaction_tags.c.processed_txn_id.in_(proc_ids)
            )
        )
        db.execute(
            delete(TransactionPersonShare).where(
                TransactionPersonShare.processed_txn_id.in_(proc_ids)
            )
        )
    db.execute(
        delete(ProcessedTransaction).where(ProcessedTransaction.user_id == user_id)
    )
    db.execute(delete(RawTransaction).where(RawTransaction.user_id == user_id))
    db.execute(delete(BudgetPlan).where(BudgetPlan.user_id == user_id))
    db.execute(delete(Tag).where(Tag.user_id == user_id))
    db.execute(delete(Person).where(Person.user_id == user_id))
    db.execute(delete(Category).where(Category.user_id == user_id))
    db.execute(delete(User).where(User.id == user_id))
    db.commit()


def get_or_create_user(db: Session, reset: bool) -> User:
    existing = db.execute(
        select(User).where(User.email == DEMO_EMAIL)
    ).scalar_one_or_none()
    if existing:
        if not reset:
            print(
                f"User {DEMO_EMAIL} already exists."
                " Re-run with --reset to wipe and reseed.",
                file=sys.stderr,
            )
            sys.exit(2)
        print(f"Wiping existing demo user {existing.id}…")
        wipe_user(db, existing.id)

    # Calendar mode matches the seeded 2026 budget against the Dec 2025 -
    # May 2026 transaction window; FY mode would put most of that data in a
    # different period bucket and the dashboard would look half-empty.
    user = User(
        email=DEMO_EMAIL,
        password_hash=hash_password(DEMO_PASSWORD),
        period_mode="calendar",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    print(f"Created user {user.email} ({user.id})")
    return user


def seed_categories(db: Session, user_id: uuid.UUID) -> dict[str, Category]:
    rows = [Category(user_id=user_id, name=n) for n in CATEGORIES]
    db.add_all(rows)
    db.commit()
    for r in rows:
        db.refresh(r)
    return {r.name: r for r in rows}


def seed_persons(db: Session, user_id: uuid.UUID) -> dict[str, Person]:
    rows = [Person(user_id=user_id, name=n) for n in PERSONS]
    db.add_all(rows)
    db.commit()
    for r in rows:
        db.refresh(r)
    return {r.name: r for r in rows}


def seed_tags(db: Session, user_id: uuid.UUID) -> dict[str, Tag]:
    rows = [Tag(user_id=user_id, name=n) for n in TAGS]
    db.add_all(rows)
    db.commit()
    for r in rows:
        db.refresh(r)
    return {r.name: r for r in rows}


def seed_budget(db: Session, user_id: uuid.UUID, cats: dict[str, Category]) -> None:
    rows = [
        BudgetPlan(
            user_id=user_id,
            year=2026,
            category_id=cats[name].id,
            allocated_amount=Decimal(amt),
        )
        for name, amt in YEARLY_BUDGET.items()
    ]
    db.add_all(rows)
    db.commit()
    print(f"Seeded budget for 2026: {len(rows)} categories")


def insert_processed(
    db: Session,
    user_id: uuid.UUID,
    txn_date: date,
    description: str,
    amount: Decimal,  # positive — sign applied below
    category: Category,
    txn_type: str,
    *,
    shares: list[tuple[Person, Decimal]] | None = None,  # (person, percent 0-100)
    tags: list[Tag] | None = None,
    notes: str | None = None,
    settled_persons: set[uuid.UUID] | None = None,
) -> ProcessedTransaction:
    raw = RawTransaction(
        user_id=user_id,
        txn_date=datetime.combine(txn_date, datetime.min.time()),
        description=description,
        amount=amount,  # raw stays signed by source — we keep the storage convention
        status="processed",
        txn_type=txn_type,
    )
    db.add(raw)
    db.flush()

    # Others' share total in percent → rupees.
    others_total = Decimal("0")
    for _, pct in shares or []:
        others_total += amount * pct / Decimal("100")
    effective = amount - others_total

    sign = -1 if txn_type in {"income", "refund", "transfer"} else 1
    signed_amount = sign * abs(amount)
    signed_effective = sign * abs(effective)

    processed = ProcessedTransaction(
        user_id=user_id,
        raw_txn_id=raw.id,
        category_id=category.id,
        txn_date=txn_date,
        description=description,
        amount=signed_amount,
        effective_amount=signed_effective,
        month=txn_date.month,
        year=txn_date.year,
        notes=notes,
        txn_type=txn_type,
    )
    db.add(processed)
    db.flush()

    for person, pct in shares or []:
        share_amt = amount * pct / Decimal("100")
        share = TransactionPersonShare(
            processed_txn_id=processed.id,
            person_id=person.id,
            share_type="percentage",
            share_value=pct,
            share_amount=sign * abs(share_amt),
            settled=person.id in (settled_persons or set()),
        )
        db.add(share)

    if tags:
        for t in tags:
            db.execute(
                transaction_tags.insert().values(
                    processed_txn_id=processed.id, tag_id=t.id
                )
            )

    # Ensure the raw row's effective amount is set correctly post sign.
    raw.amount = signed_amount
    return processed


def seed_transactions(
    db: Session,
    user_id: uuid.UUID,
    cats: dict[str, Category],
    persons: dict[str, Person],
    tags: dict[str, Tag],
    today: date,
) -> int:
    count = 0
    aarav = persons["Aarav"]
    priya = persons["Priya"]
    work_tag = tags["Work"]
    family_tag = tags["Family"]
    reimb_tag = tags["Reimbursable"]
    vacation_tag = tags["Vacation"]

    for year, month in months_back(6, today):
        is_current_month = (year, month) == (today.year, today.month)

        # ── Fixed monthly anchors ────────────────────────────────────────────
        for desc, cat_name, amt, day in MONTHLY_FIXED:
            try:
                d = date(year, month, day)
            except ValueError:
                continue
            if d > today:
                continue
            if cat_name == "Salary":
                insert_processed(
                    db,
                    user_id,
                    d,
                    desc,
                    Decimal(amt),
                    cats[cat_name],
                    "income",
                    tags=[work_tag],
                    notes="Monthly salary credit",
                )
            elif cat_name == "Rent":
                insert_processed(
                    db,
                    user_id,
                    d,
                    desc,
                    Decimal(amt),
                    cats[cat_name],
                    "expense",
                    shares=[(aarav, Decimal("50"))],
                    settled_persons={aarav.id} if month < today.month else None,
                )
            else:
                insert_processed(
                    db,
                    user_id,
                    d,
                    desc,
                    Decimal(amt),
                    cats[cat_name],
                    "expense",
                )
            count += 1

        # ── Variable monthly spend ───────────────────────────────────────────
        category_volume = {
            "Groceries": random.randint(4, 7),
            "Food & Dining": random.randint(8, 14),
            "Transport": random.randint(6, 11),
            "Shopping": random.randint(2, 4),
            "Health": random.randint(0, 2),
            "Entertainment": random.randint(1, 3),
            "Personal Care": random.randint(0, 2),
            "Education": random.randint(0, 1),
        }
        for cat_name, n in category_volume.items():
            for _ in range(n):
                d = random_day_in_month(year, month, today)
                desc, amt = pick_merchant(cat_name)
                txn_tags: list[Tag] = []
                if cat_name == "Food & Dining" and random.random() < 0.15:
                    txn_tags = [reimb_tag]
                if cat_name == "Shopping" and random.random() < 0.25:
                    txn_tags = [family_tag]
                insert_processed(
                    db,
                    user_id,
                    d,
                    desc,
                    amt,
                    cats[cat_name],
                    "expense",
                    tags=txn_tags,
                )
                count += 1

        # ── Occasional shared dinner with Priya (60/40 split) ────────────────
        if random.random() < 0.7:
            d = random_day_in_month(year, month, today)
            desc, amt = pick_merchant("Food & Dining")
            insert_processed(
                db,
                user_id,
                d,
                f"{desc} (split with Priya)",
                amt,
                cats["Food & Dining"],
                "expense",
                shares=[(priya, Decimal("40"))],
                settled_persons={priya.id} if month < today.month - 1 else None,
            )
            count += 1

        # ── Once-a-quarter travel ────────────────────────────────────────────
        if month in {1, 4, 12}:
            d = random_day_in_month(year, month, today)
            desc, amt = pick_merchant("Travel")
            insert_processed(
                db,
                user_id,
                d,
                desc,
                amt,
                cats["Travel"],
                "expense",
                tags=[vacation_tag],
            )
            count += 1

        # ── Freelance income (some months) ──────────────────────────────────
        if random.random() < 0.6:
            d = date(year, month, random.randint(15, 25))
            if d <= today:
                desc, amt = pick_income("Freelance")
                insert_processed(
                    db,
                    user_id,
                    d,
                    desc,
                    amt,
                    cats["Freelance"],
                    "income",
                    tags=[work_tag],
                )
                count += 1

        # ── Investment income (every 2 months) ──────────────────────────────
        if month % 2 == 0:
            d = date(year, month, 28 if month != 2 else 25)
            if d <= today:
                desc, amt = pick_income("Investment Income")
                insert_processed(
                    db,
                    user_id,
                    d,
                    desc,
                    amt,
                    cats["Investment Income"],
                    "income",
                )
                count += 1

        # ── Refund here and there ────────────────────────────────────────────
        if random.random() < 0.4:
            d = random_day_in_month(year, month, today)
            insert_processed(
                db,
                user_id,
                d,
                "Amazon Refund",
                Decimal(random.randint(300, 1800)),
                cats["Shopping"],
                "refund",
            )
            count += 1

        # ── Pending raw transactions in the current month ────────────────────
        if is_current_month:
            for _ in range(4):
                d = random_day_in_month(year, month, today)
                cat_pick = random.choice(["Food & Dining", "Transport", "Shopping"])
                desc, amt = pick_merchant(cat_pick)
                db.add(
                    RawTransaction(
                        user_id=user_id,
                        txn_date=datetime.combine(d, datetime.min.time()),
                        description=desc,
                        amount=amt,
                        status="pending",
                        txn_type=None,
                    )
                )
                count += 1

    db.commit()
    return count


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the existing demo user (if any) before seeding.",
    )
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        sys.exit(1)

    engine = create_engine(db_url, future=True)
    with Session(engine) as db:
        user = get_or_create_user(db, reset=args.reset)
        cats = seed_categories(db, user.id)
        persons = seed_persons(db, user.id)
        tags = seed_tags(db, user.id)
        seed_budget(db, user.id, cats)
        today = datetime.now(timezone.utc).date()
        n = seed_transactions(db, user.id, cats, persons, tags, today)
        print(f"Seeded {n} transactions across 6 months")

    print("─" * 60)
    print("Demo account ready:")
    print(f"  email:    {DEMO_EMAIL}")
    print(f"  password: {DEMO_PASSWORD}")
    print("─" * 60)


if __name__ == "__main__":
    main()
