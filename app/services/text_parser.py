"""
Text-based bank statement parser.

Supports HDFC credit card web portal copy-paste format:
  <DD Mon YYYY>\t\n
  <merchant name>\n
  [optional extra lines like "ELIGIBLE FOR SMARTEMI"]\n
  ₹X,XXX.XX\t(credit|debit) icon\n

State machine: EXPECTING_DATE → EXPECTING_DESC → EXPECTING_AMOUNT → repeat

Public API:
  parse_bank_statement_text(text: str) -> ParseResult
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum, auto
from typing import Optional

from app.services.pdf_parser import ParseResult, ParsedRow


# ─── Patterns ─────────────────────────────────────────────────────────────────

_DATE_RE = re.compile(
    r"^(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})\s*$",
    re.IGNORECASE,
)

_AMOUNT_RE = re.compile(
    r"[₹]?\s*([\d,]+\.?\d{0,2})\s*(?:\t|\s)*(credit|debit)?\s*(?:icon)?",
    re.IGNORECASE,
)

_MONTH_MAP = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


class _State(Enum):
    EXPECTING_DATE = auto()
    EXPECTING_DESC = auto()
    EXPECTING_AMOUNT = auto()


def _parse_date_line(line: str) -> Optional[datetime]:
    m = _DATE_RE.match(line.strip())
    if not m:
        return None
    day = int(m.group(1))
    month = _MONTH_MAP[m.group(2).lower()]
    year = int(m.group(3))
    return datetime(year, month, day)


def _parse_amount_line(line: str) -> Optional[float]:
    """
    Parse an amount line like '₹1,234.56\tcredit' or '₹449.00\tdebit icon'.
    Returns positive float (both credit and debit imported as positive amounts,
    matching PDF parser convention for raw transactions).
    """
    m = _AMOUNT_RE.search(line.strip())
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_bank_statement_text(text: str) -> ParseResult:
    """
    Parse HDFC credit card web portal copy-paste text into a ParseResult.

    Both credit and debit rows are imported as positive amounts to match the
    convention used by the PDF parser for raw transactions.
    """
    result = ParseResult()
    state = _State.EXPECTING_DATE
    current_date: Optional[datetime] = None
    desc_lines: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if state == _State.EXPECTING_DATE:
            dt = _parse_date_line(line)
            if dt is not None:
                current_date = dt
                desc_lines = []
                state = _State.EXPECTING_DESC
            # else: skip non-date lines before the first transaction

        elif state == _State.EXPECTING_DESC:
            # Check if this line is an amount (e.g. the description was on the
            # same conceptual block but we got to the amount immediately)
            amount = _parse_amount_line(line)
            if amount is not None:
                if desc_lines:
                    result.rows.append(
                        ParsedRow(
                            txn_date=current_date,
                            description=" ".join(desc_lines).strip(),
                            amount=amount,
                        )
                    )
                else:
                    result.skipped += 1
                    result.skipped_rows.append(
                        f"{current_date.strftime('%d %b %Y')}: (no description)"
                    )
                state = _State.EXPECTING_DATE
                current_date = None
                desc_lines = []
            else:
                desc_lines.append(line)
                state = _State.EXPECTING_AMOUNT

        elif state == _State.EXPECTING_AMOUNT:
            amount = _parse_amount_line(line)
            if amount is not None:
                description = " ".join(desc_lines).strip()
                result.rows.append(
                    ParsedRow(
                        txn_date=current_date,
                        description=description,
                        amount=amount,
                    )
                )
                state = _State.EXPECTING_DATE
                current_date = None
                desc_lines = []
            else:
                # Additional description line (e.g. "ELIGIBLE FOR SMARTEMI")
                # — only append if it doesn't look like a new date
                dt = _parse_date_line(line)
                if dt is not None:
                    # Missed the amount; skip incomplete transaction, start fresh
                    result.skipped += 1
                    desc = " ".join(desc_lines).strip()
                    result.skipped_rows.append(
                        f"{current_date.strftime('%d %b %Y')}: {desc} (no amount found)"
                    )
                    current_date = dt
                    desc_lines = []
                    state = _State.EXPECTING_DESC
                else:
                    desc_lines.append(line)

    # Handle any incomplete transaction at end of input
    if state != _State.EXPECTING_DATE and current_date is not None:
        result.skipped += 1
        desc = " ".join(desc_lines).strip()
        result.skipped_rows.append(
            f"{current_date.strftime('%d %b %Y')}: {desc} (incomplete — end of input)"
            if desc
            else f"{current_date.strftime('%d %b %Y')}: (incomplete — end of input)"
        )

    return result
