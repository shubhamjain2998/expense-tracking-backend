"""One-time backfill: teach existing category mappings the tags and split
their transactions already show, and repair rows severed from their rule.

Why this exists
---------------
Mappings used to store only a category. Tags and splits were copied on the
client from whatever the React Query cache happened to hold, so a transaction
inherited them only when a sibling row was already loaded in the browser.
Now the server applies a rule whole. This script derives what each rule should
have been carrying all along, from the transactions the user actually
categorised.

It runs in four phases, each independently skippable:

  1. enrich  — give existing rules the majority tag set and split of the
               transactions that match them.
  2. create  — add rules for description clusters that have no rule at all,
               above --min-cluster occurrences.
  3. relink  — set mapping_id on processed rows that match a rule but lost
               the link (a restore used to null it).
  4. fill    — apply a rule's tags and split to matching rows that have
               NEITHER tags NOR a split. Never overwrites an existing choice.

Only phase 4 can change money (a split moves effective_amount), and only for
rows that had no split at all. Everything is reported before anything is
written.

Usage
-----
    venv/bin/python scripts/backfill_mapping_context.py --user-email you@x.com
    venv/bin/python scripts/backfill_mapping_context.py --user-email you@x.com --apply

Dry run is the default. --apply writes inside a single transaction.
"""

import argparse
import os
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uuid  # noqa: E402

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session, selectinload  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    CategoryMapping,
    CategoryMappingShare,
    ProcessedTransaction,
    TransactionPersonShare,
    User,
)
from app.services.normalizer import normalize_description  # noqa: E402

from rapidfuzz import fuzz  # noqa: E402

# Same threshold auto-categorise uses. Deriving rules under a looser rule than
# the one that will apply them would produce rules that never fire.
MATCH_THRESHOLD = 80

# A majority of one out of two is a coin flip, not evidence. A derived value
# has to be both strictly more common than everything else combined and backed
# by at least this many transactions.
MIN_AGREEMENT = 2

# A share, in the shape that compares and hashes cleanly.
ShareKey = Tuple[uuid.UUID, str, Decimal]


def _share_key(share) -> ShareKey:
    # Shares are stored signed (a refund's share is negative); the rule stores
    # the magnitude, exactly as the user typed it.
    return (
        share.person_id,
        share.share_type,
        abs(Decimal(str(share.share_value))),
    )


def _tag_key(txn) -> Tuple[uuid.UUID, ...]:
    return tuple(sorted(t.id for t in txn.tags))


def _best_mapping(
    description: str, normalised_patterns: Sequence[Tuple[CategoryMapping, str]]
) -> Optional[CategoryMapping]:
    nd = normalize_description(description)
    best_score = 0
    best = None
    for mapping, pattern in normalised_patterns:
        score = fuzz.token_sort_ratio(nd, pattern)
        if score > best_score:
            best_score = score
            best = mapping
    return best if best_score >= MATCH_THRESHOLD else None


def _majority(counter: Counter):
    """The most common value, and how dominant it was."""
    if not counter:
        return None, 0, 0
    value, count = counter.most_common(1)[0]
    return value, count, sum(counter.values())


class Report:
    def __init__(self) -> None:
        self.lines: List[str] = []
        self.counts: Counter = Counter()

    def add(self, phase: str, line: str) -> None:
        self.lines.append(f"  {line}")
        self.counts[phase] += 1

    def section(self, title: str) -> None:
        self.lines.append("")
        self.lines.append(title)


def backfill(
    db: Session,
    user_id: uuid.UUID,
    *,
    apply: bool,
    min_cluster: int,
    phases: Sequence[str],
) -> Report:
    report = Report()

    mappings = (
        db.execute(
            select(CategoryMapping)
            .where(CategoryMapping.user_id == user_id)
            .options(
                selectinload(CategoryMapping.tags),
                selectinload(CategoryMapping.shares),
            )
        )
        .scalars()
        .all()
    )
    txns = (
        db.execute(
            select(ProcessedTransaction)
            .where(ProcessedTransaction.user_id == user_id)
            .options(
                selectinload(ProcessedTransaction.tags),
                selectinload(ProcessedTransaction.shares),
            )
        )
        .scalars()
        .all()
    )
    print(f"user {user_id}: {len(mappings)} rules, {len(txns)} transactions")

    normalised = [(m, normalize_description(m.description_pattern)) for m in mappings]
    matched: Dict[uuid.UUID, List[ProcessedTransaction]] = defaultdict(list)
    unmatched: List[ProcessedTransaction] = []
    for txn in txns:
        m = _best_mapping(txn.description, normalised)
        if m is None:
            unmatched.append(txn)
        else:
            matched[m.id].append(txn)

    by_id = {m.id: m for m in mappings}

    # What each rule will carry once phase 1 and 2 are done. Phase 4 reads
    # this, not the ORM objects, so the dry run and the apply run agree.
    effective: Dict[uuid.UUID, Tuple[list, list]] = {}

    # ── Phase 1: enrich existing rules ───────────────────────────────────
    if "enrich" in phases:
        report.section("PHASE 1 — enrich existing rules")
        for mapping_id, rows in matched.items():
            mapping = by_id[mapping_id]
            effective[mapping_id] = _enrich(mapping, rows, db, report, apply)

    # ── Phase 2: rules for clusters that have none ───────────────────────
    created: List[CategoryMapping] = []
    if "create" in phases:
        report.section(
            f"PHASE 2 — new rules for clusters of {min_cluster}+ transactions"
        )
        clusters: Dict[str, List[ProcessedTransaction]] = defaultdict(list)
        for txn in unmatched:
            clusters[normalize_description(txn.description)].append(txn)

        for key, rows in sorted(clusters.items(), key=lambda kv: -len(kv[1])):
            if len(rows) < min_cluster:
                continue
            cat_counter = Counter(t.category_id for t in rows)
            category_id, cat_hits, cat_total = _majority(cat_counter)
            # The pattern is a real description, not the normalised key: the
            # normaliser strips detail the matcher still wants to see.
            pattern = Counter(t.description.strip() for t in rows).most_common(1)[0][0]
            report.add(
                "create",
                f"+ {pattern!r} ({len(rows)} txns, "
                f"category {cat_hits}/{cat_total} agree)",
            )
            if apply:
                mapping = CategoryMapping(
                    user_id=user_id,
                    description_pattern=pattern,
                    category_id=category_id,
                    match_count=0,
                    last_used=None,
                )
                db.add(mapping)
                db.flush()
                created.append(mapping)
                matched[mapping.id] = rows
                by_id[mapping.id] = mapping
                effective[mapping.id] = _enrich(
                    mapping, rows, db, report, apply, quiet=True
                )
            else:
                effective[uuid.uuid4()] = _preview_enrich(rows, report, pattern)

    # ── Phase 3: repair the mapping_id link ──────────────────────────────
    if "relink" in phases:
        report.section("PHASE 3 — relink transactions to their rule")
        relinked = 0
        for mapping_id, rows in matched.items():
            for txn in rows:
                if txn.mapping_id != mapping_id:
                    relinked += 1
                    if apply:
                        txn.mapping_id = mapping_id
        report.add("relink", f"{relinked} transactions relinked")

    # ── Phase 4: fill gaps ───────────────────────────────────────────────
    if "fill" in phases:
        report.section("PHASE 4 — fill transactions that have no tags and no split")
        for mapping_id, rows in matched.items():
            rule_tags, rule_shares = effective.get(
                mapping_id,
                (
                    list(by_id[mapping_id].tags),
                    [_share_key(s) for s in by_id[mapping_id].shares],
                ),
            )
            _fill(rule_tags, rule_shares, rows, db, report, apply)

    return report


def _is_evidence(hits: int, total: int, min_agreement: int) -> bool:
    return hits >= min_agreement and hits * 2 > total


def _derive(rows: Sequence[ProcessedTransaction], min_agreement: int = MIN_AGREEMENT):
    """The tag set and split the majority of these transactions share.

    Returns empty when the rows disagree too much to call — a rule left blank
    is recoverable, a rule that tags 60 future transactions wrongly is tedious
    to undo.
    """
    tag_counter = Counter(_tag_key(t) for t in rows)
    share_counter = Counter(
        tuple(sorted(_share_key(s) for s in t.shares)) for t in rows
    )
    tags, tag_hits, tag_total = _majority(tag_counter)
    shares, share_hits, share_total = _majority(share_counter)
    if not _is_evidence(tag_hits, tag_total, min_agreement):
        tags = ()
    if not _is_evidence(share_hits, share_total, min_agreement):
        shares = ()
    return (tags or (), tag_hits, tag_total), (shares or (), share_hits, share_total)


def _preview_enrich(rows, report: Report, label: str):
    """Describe the rule phase 2 would create, without creating it."""
    (tag_ids, th, tt), (shares, sh, st) = _derive(rows)
    tag_lookup = {t.id: t for row in rows for t in row.tags}
    tags = [tag_lookup[tid] for tid in tag_ids]
    if tags:
        names = ", ".join(sorted(t.name for t in tags))
        report.add("create", f"    tags [{names}] ({th}/{tt} agree)")
    if shares:
        report.add("create", f"    split across {len(shares)} person(s) ({sh}/{st})")
    return tags, list(shares)


def _enrich(
    mapping: CategoryMapping,
    rows: Sequence[ProcessedTransaction],
    db: Session,
    report: Report,
    apply: bool,
    quiet: bool = False,
):
    """Give a rule the majority tag set and split of its transactions.

    Only fills what the rule does not already have: a rule the user has
    already edited by hand is never second-guessed.

    Returns the context the rule ends up carrying — its own where it already
    had one, the derived one otherwise — so that a dry run can report phase 4
    against the same rule an --apply run would use.
    """
    (tag_ids, tag_hits, tag_total), (shares, share_hits, share_total) = _derive(rows)

    tag_lookup = {t.id: t for row in rows for t in row.tags}

    effective_tags = [t for t in mapping.tags]
    effective_shares = [_share_key(s) for s in mapping.shares]

    if tag_ids and not mapping.tags:
        effective_tags = [tag_lookup[tid] for tid in tag_ids]
    if shares and not mapping.shares:
        effective_shares = list(shares)

    if tag_ids and not mapping.tags:
        names = ", ".join(sorted(tag_lookup[tid].name for tid in tag_ids))
        if not quiet:
            report.add(
                "enrich",
                f"{mapping.description_pattern!r} -> tags [{names}] "
                f"({tag_hits}/{tag_total} of its {len(rows)} txns agree)",
            )
        if apply:
            mapping.tags = [tag_lookup[tid] for tid in tag_ids]

    if shares and not mapping.shares:
        if not quiet:
            report.add(
                "enrich",
                f"{mapping.description_pattern!r} -> split across "
                f"{len(shares)} person(s) "
                f"({share_hits}/{share_total} agree)",
            )
        if apply:
            mapping.shares = [
                CategoryMappingShare(
                    mapping_id=mapping.id,
                    person_id=person_id,
                    share_type=share_type,
                    share_value=float(share_value),
                )
                for person_id, share_type, share_value in shares
            ]

    return effective_tags, effective_shares


def _fill(
    rule_tags,
    rule_shares: Sequence[ShareKey],
    rows: Sequence[ProcessedTransaction],
    db: Session,
    report: Report,
    apply: bool,
) -> None:
    if not rule_tags and not rule_shares:
        return

    for txn in rows:
        if txn.tags or txn.shares:
            continue  # the user made a choice here; leave it

        parts = []
        if rule_tags:
            names = ", ".join(sorted(t.name for t in rule_tags))
            parts.append(f"tags [{names}]")
            if apply:
                txn.tags = list(rule_tags)

        if rule_shares:
            total = abs(Decimal(str(txn.amount)))
            sign = -1 if Decimal(str(txn.amount)) < 0 else 1
            others = Decimal("0")
            records = []
            for person_id, share_type, share_value in rule_shares:
                value = abs(Decimal(str(share_value)))
                if share_type == "percentage":
                    amount = total * value / Decimal("100")
                else:
                    amount = value
                others += amount
                records.append(
                    TransactionPersonShare(
                        processed_txn_id=txn.id,
                        person_id=person_id,
                        share_type=share_type,
                        share_value=float(value),
                        share_amount=float(sign * amount),
                        settled=False,
                    )
                )
            new_effective = sign * (total - others)
            parts.append(
                f"effective {Decimal(str(txn.effective_amount))} -> {new_effective}"
            )
            if apply:
                for record in records:
                    db.add(record)
                txn.effective_amount = float(new_effective)

        report.add(
            "fill",
            f"{txn.txn_date} {txn.description[:40]!r}: {', '.join(parts)}",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user-email")
    group.add_argument("--user-id")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the changes (default is a dry run that writes nothing)",
    )
    parser.add_argument(
        "--min-cluster",
        type=int,
        default=3,
        help="how many transactions a description needs before it earns a rule",
    )
    parser.add_argument(
        "--phases",
        default="enrich,create,relink,fill",
        help="comma-separated subset of: enrich, create, relink, fill",
    )
    parser.add_argument(
        "--limit-report",
        type=int,
        default=0,
        help="print at most N lines per phase (0 = all)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.user_id:
            user_id = uuid.UUID(args.user_id)
        else:
            user = db.execute(
                select(User).where(User.email == args.user_email)
            ).scalar_one_or_none()
            if user is None:
                sys.exit(f"no user with email {args.user_email}")
            user_id = user.id

        phases = [p.strip() for p in args.phases.split(",") if p.strip()]
        report = backfill(
            db,
            user_id,
            apply=args.apply,
            min_cluster=args.min_cluster,
            phases=phases,
        )

        printed: Counter = Counter()
        current = ""
        for line in report.lines:
            if not line.startswith("  "):
                current = line
                printed[current] = 0
                print(line)
                continue
            printed[current] += 1
            if args.limit_report and printed[current] > args.limit_report:
                continue
            print(line)
        for section, shown in printed.items():
            if args.limit_report and shown > args.limit_report:
                print(f"  … {shown - args.limit_report} more under {section.strip()}")

        print("")
        print("summary:", dict(report.counts))

        if args.apply:
            db.commit()
            print("committed")
        else:
            db.rollback()
            print("DRY RUN — nothing written. Re-run with --apply.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
