"""
PDF bank-statement parser.

Supports three table layouts:
  1. Single-column  — HDFC credit-card style where every row is one merged text cell:
       DD/MM/YYYY| HH:MM  MERCHANT [+ N C]  C AMOUNT.XX [l]
  2. Multi-column   — separate date / description / debit / credit / amount columns,
       with one row per transaction.
  3. Merged multi-column — same 7-column layout as (2) but pdfplumber collapses all
       transactions in a page into a single row, with newline-separated values per
       cell (common in HDFC savings-account statements).  These rows are expanded
       into individual rows before parsing.

Public API used by the router and the playground notebook:
  parse_bank_statement(pdf_bytes) -> ParseResult
  parse_date(s)                   -> Optional[date]
  parse_amount(s)                 -> Optional[float]
  _find_header_row(table)         -> (header_idx, ColumnMap | None)
  _detect_columns_by_heuristic(table) -> ColumnMap | None
  _is_transaction_table(table)    -> bool
  _parse_table(table, fallback_col_map) -> (rows, skipped_count, col_map)
  ColumnMap, ParsedRow, ParseResult
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Tuple

import pdfplumber


# ─── Data models ──────────────────────────────────────────────────────────────


@dataclass
class ColumnMap:
    date_col: Optional[int]
    desc_col: Optional[int]
    debit_col: Optional[int]
    credit_col: Optional[int]
    amount_col: Optional[int]


@dataclass
class ParsedRow:
    txn_date: datetime
    description: str
    amount: float  # positive = debit/expense, negative = credit/income


@dataclass
class ParseResult:
    rows: List[ParsedRow] = field(default_factory=list)
    skipped: int = 0
    warnings: List[str] = field(default_factory=list)


# ─── Date parsing ─────────────────────────────────────────────────────────────

_DATE_FORMATS = [
    "%d/%m/%Y",  # 19/01/2026
    "%d-%m-%Y",  # 19-01-2026
    "%d/%m/%y",  # 19/01/26
    "%d-%m-%y",  # 19-01-26
    "%d %b %Y",  # 19 Jan 2026
    "%d-%b-%Y",  # 19-Jan-2026
    "%d/%b/%Y",  # 19/Jan/2026
    "%Y-%m-%d",  # 2026-01-19
    "%d%b%y",  # 19Jan26
    "%d %b, %Y",  # 19 Jan, 2026
    "%d %B %Y",  # 19 January 2026
]

_DATE_SEARCH_RE = re.compile(
    r"(\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"
    r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"[a-z]*\s*,?\s*\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\d{2})",
    re.IGNORECASE,
)


def parse_date(s: str) -> Optional[date]:
    """Parse a date string into a Python date.  Returns None on failure."""
    if not s:
        return None
    s = s.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    # Try extracting a date-like sub-string and retry
    m = _DATE_SEARCH_RE.search(s)
    if m and m.group(0) != s:
        return parse_date(m.group(0))
    return None


# ─── Amount parsing ───────────────────────────────────────────────────────────

_PARENS_RE = re.compile(r"^\((.+)\)$")
_CREDIT_SUFFIX_RE = re.compile(r"\s*(CR|Cr)\b")
_DEBIT_SUFFIX_RE = re.compile(r"\s*(DR|Dr)\b")
_BARE_NUMBER_RE = re.compile(r"[\d,]+\.?\d*")


def parse_amount(s: str) -> Optional[float]:
    """
    Parse an amount string to float.
    Positive = debit/expense, negative = credit/income.

    Handles: 1,234.56 / 1,234.56 DR / 1,234.56 CR / (500.00) / 50000CR / -
    """
    if not s:
        return None
    s = s.strip()
    if s in ("-", "", "N/A", "Nil"):
        return None

    is_credit = False

    # (500.00) → negative
    m = _PARENS_RE.match(s)
    if m:
        s = m.group(1)
        is_credit = True

    # CR / DR suffix
    if _CREDIT_SUFFIX_RE.search(s):
        is_credit = True
        s = _CREDIT_SUFFIX_RE.sub("", s)
    elif _DEBIT_SUFFIX_RE.search(s):
        s = _DEBIT_SUFFIX_RE.sub("", s)

    # Explicit leading minus
    if s.startswith("-"):
        is_credit = True
        s = s[1:]

    # Strip currency symbols, spaces
    s = re.sub(r"[₹$€£,\s]", "", s)

    m = _BARE_NUMBER_RE.search(s)
    if not m:
        return None
    try:
        val = float(m.group(0).replace(",", ""))
    except ValueError:
        return None

    return -val if is_credit else val


# ─── Column-map helpers ────────────────────────────────────────────────────────

# Patterns for each column role.
# "withdrawal" and "deposit" are intentionally matched without a trailing \b so
# that concatenated headers like "WithdrawalAmt." and "DepositAmt." are detected.
_HDR = {
    "date": re.compile(r"\bdate\b", re.IGNORECASE),
    "desc": re.compile(
        r"\b(description|narration|particulars|transaction|details|remarks)\b",
        re.IGNORECASE,
    ),
    "debit": re.compile(r"\bdebit\b|\bdr\.?\b|withdrawal", re.IGNORECASE),
    "credit": re.compile(r"\bcredit\b|\bcr\.?\b|deposit", re.IGNORECASE),
    "amount": re.compile(r"\b(amount|amt)\b", re.IGNORECASE),
}

_DATE_VALUE_RE = re.compile(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b")
_AMOUNT_VALUE_RE = re.compile(r"^\s*[\d,]+\.\d{2}\s*$")
_AMOUNT_SEARCH_RE = re.compile(r"\b[\d,]+\.\d{2}\b")


def _find_header_row(
    table: List[List[str]],
) -> Tuple[Optional[int], Optional[ColumnMap]]:
    """
    Scan the first 6 rows for a header.
    Returns (header_row_index, ColumnMap) or (None, None).
    """
    for row_idx, row in enumerate(table[:6]):
        row_text = " ".join(c for c in row if c)
        has_date = bool(_HDR["date"].search(row_text))
        has_desc = bool(_HDR["desc"].search(row_text))
        has_amount = bool(_HDR["amount"].search(row_text))
        has_debit = bool(_HDR["debit"].search(row_text))

        # Need at minimum: date + (description OR amount/debit column)
        if not (has_date and (has_desc or has_amount or has_debit)):
            continue

        date_col = desc_col = debit_col = credit_col = amount_col = None
        for col_idx, cell in enumerate(row):
            cell = cell or ""
            if _HDR["date"].search(cell) and date_col is None:
                date_col = col_idx
            if _HDR["desc"].search(cell) and desc_col is None:
                desc_col = col_idx
            if _HDR["debit"].search(cell) and debit_col is None:
                debit_col = col_idx
            if _HDR["credit"].search(cell) and credit_col is None:
                credit_col = col_idx
            if _HDR["amount"].search(cell) and amount_col is None:
                amount_col = col_idx

        return row_idx, ColumnMap(
            date_col=date_col,
            desc_col=desc_col,
            debit_col=debit_col,
            credit_col=credit_col,
            amount_col=amount_col,
        )

    return None, None


def _detect_columns_by_heuristic(
    table: List[List[str]],
) -> Optional[ColumnMap]:
    """
    Guess column roles from data values when no header is present.
    Only works reliably for multi-column tables (ncols >= 2).
    """
    if not table or not table[0]:
        return None
    ncols = max(len(row) for row in table)
    if ncols < 2:
        return None

    date_votes = [0] * ncols
    amount_votes = [0] * ncols
    text_votes = [0] * ncols

    for row in table:
        for col_idx, cell in enumerate(row):
            if col_idx >= ncols:
                continue
            cell = (cell or "").strip()
            if _DATE_VALUE_RE.search(cell):
                date_votes[col_idx] += 1
            if _AMOUNT_VALUE_RE.match(cell):
                amount_votes[col_idx] += 1
            elif len(cell) > 8 and not re.sub(r"[,.\s]", "", cell).isdigit():
                text_votes[col_idx] += 1

    date_col = date_votes.index(max(date_votes)) if max(date_votes) > 0 else None
    amount_col = None
    for i in range(ncols - 1, -1, -1):
        if amount_votes[i] > 0:
            amount_col = i
            break
    desc_col = None
    for i in range(ncols):
        if i in (date_col, amount_col):
            continue
        if text_votes[i] > 0:
            desc_col = i
            break

    if date_col is None and amount_col is None:
        return None

    return ColumnMap(
        date_col=date_col,
        desc_col=desc_col,
        debit_col=None,
        credit_col=None,
        amount_col=amount_col,
    )


# ─── Single-column (HDFC merged) row parser ───────────────────────────────────
#
# Cell format: DD/MM/YYYY| HH:MM  MERCHANT [+ N C]  C AMOUNT.XX [l]
# "C" before the amount = ₹ (rupee symbol garbled by the PDF renderer)
# "+ N C" before the amount = reward-points info (N pts earned)
# "l" after the amount = trailing artefact (ignore)

_DATE_TIME_RE = re.compile(r"(\d{2}/\d{2}/\d{4})\s*\|\s*(\d{2}:\d{2})\s+")

# Amount in the row: [+] C DIGITS [l|I]
# A leading "+" before the rupee symbol (C) means the transaction is a credit.
# No "$" anchor — some rows append a reference number after the amount.
# We always take the LAST match so we land on the actual transaction amount.
_HDFC_AMOUNT_RE = re.compile(r"(\+\s*)?C\s+([\d,]+\.?\d{0,2})\s*[lI]?\s*")

# Reward-points artefact: "+ N C" or "+ N" at end of description
_REWARD_PTS_RE = re.compile(r"\s*\+\s*\d+\s*C?\s*$")

# Transactions that are payments / credits back to the card (→ negative amount)
_CREDIT_DESC_RE = re.compile(
    r"\b(CREDIT\s+CARD\s+PAYMENT|PAYMENT\s+RECEIVED|NEFT\s+(CR|PAYMENT)|"
    r"IMPS\s+CR|REFUND|REVERSAL|CASHBACK|CHARGEBACK|ADJUSTMENT\s+CREDIT)\b",
    re.IGNORECASE,
)


def _parse_single_column_row(cell_text: str) -> Optional[ParsedRow]:
    """
    Parse one cell from an HDFC-style single-column transaction table.

    Handles:
      - Normal rows:   "DD/MM/YYYY| HH:MM MERCHANT C AMOUNT l"
      - Reward rows:   "DD/MM/YYYY| HH:MM MERCHANT + 8 C AMOUNT l"
      - Prefix rows:   "SHUBHAM JAIN\\nDD/MM/YYYY| HH:MM MERCHANT C AMOUNT l"
      - Suffix rows:   "DESCRIPTION (Ref#\\nDD/MM/YYYY | HH:MM C AMOUNT l"
    """
    if not cell_text or not cell_text.strip():
        return None

    # Flatten multi-line content into a single string
    text = " ".join(line.strip() for line in cell_text.split("\n") if line.strip())

    # ── Locate date+time anywhere in the text ─────────────────────────────────
    dt_m = _DATE_TIME_RE.search(text)
    if not dt_m:
        return None

    d = parse_date(dt_m.group(1))
    if d is None:
        return None
    h, m = map(int, dt_m.group(2).split(":"))
    txn_date = datetime(d.year, d.month, d.day, h, m)

    # ── Extract amount: last "C DIGITS" in the text ───────────────────────────
    # Some rows append a reference number after the amount, so we can't anchor
    # at "$". Taking the last match ensures we get the transaction amount and
    # not an accidental earlier hit.
    amount_m = None
    for _m in _HDFC_AMOUNT_RE.finditer(text):
        amount_m = _m
    if not amount_m:
        return None

    raw_amount = float(amount_m.group(2).replace(",", ""))

    # ── Extract description ───────────────────────────────────────────────────
    # Primary: text between end-of-datetime and start-of-amount-marker
    after_dt = text[dt_m.end() :]  # noqa: E203
    desc_raw = after_dt[: amount_m.start() - (len(text) - len(after_dt))].strip()
    # Trim the reward-points artefact from the description tail
    desc_clean = _REWARD_PTS_RE.sub("", desc_raw).strip()

    # Fallback: if nothing came after the datetime (description precedes the date)
    if not desc_clean:
        desc_clean = text[: dt_m.start()].strip().rstrip("(").strip()

    if not desc_clean:
        return None

    # ── Sign convention ───────────────────────────────────────────────────────
    # Positive = debit / expense; negative = credit / payment back to card.
    # A "+" prefix on the rupee symbol is the primary signal for credits.
    # Keyword matching on the description serves as a fallback.
    has_plus = bool(amount_m.group(1))
    is_credit = has_plus or bool(_CREDIT_DESC_RE.search(text))
    amount = -raw_amount if is_credit else raw_amount

    return ParsedRow(txn_date=txn_date, description=desc_clean, amount=amount)


# ─── Multi-column row parser ───────────────────────────────────────────────────


def _parse_multi_column_row(row: List[str], col_map: ColumnMap) -> Optional[ParsedRow]:
    """Parse one row from a standard multi-column statement table."""

    def get(col: Optional[int]) -> str:
        if col is None or col >= len(row):
            return ""
        return (row[col] or "").strip()

    txn_date = parse_date(get(col_map.date_col))
    if txn_date is None:
        return None

    desc = get(col_map.desc_col) or get(col_map.date_col)

    if col_map.debit_col is not None and col_map.credit_col is not None:
        debit = parse_amount(get(col_map.debit_col))
        credit = parse_amount(get(col_map.credit_col))
        if debit is not None and debit != 0:
            amount = abs(debit)  # positive = expense
        elif credit is not None and credit != 0:
            amount = -abs(credit)  # negative = income
        else:
            return None
    elif col_map.amount_col is not None:
        amount = parse_amount(get(col_map.amount_col))
        if amount is None:
            return None
    else:
        return None

    return ParsedRow(txn_date=txn_date, description=desc, amount=amount)


# ─── Merged multi-column row handling ─────────────────────────────────────────
#
# Some PDF renderers (e.g. HDFC savings-account statements) collapse all
# transactions on a page into a *single* table row where each column cell
# contains newline-separated values — one value per transaction.
#
# Strategy:
#   1. Use the date column to count N transactions.
#   2. Locate the closing-balance column (the rightmost column that has exactly
#      N numeric values) and compute the balance delta for each row to decide
#      whether a transaction is a withdrawal (debit) or deposit (credit).
#   3. Distribute withdrawal/deposit amounts in the correct order.
#   4. Split the narration/description cell into N blocks using known
#      transaction-type prefixes as block-start markers.

# Patterns that mark the start of a new transaction in the narration column.
# Intentionally loose to survive pdfplumber's space-stripping.
_NARRATION_TXNSTART_RE = re.compile(
    r"^(UPI-?|ACH\s*[DC]?[-\s]|NEFT\s*(?:CR|DR)?|RTGS|IB\s*BILLPAY|IMPS|ATM|"
    r"INFT|MMT|CLG|CHQ|ECS|AUTO\s*SWEEP|INTEREST|SALARY|\d{8,}[-\s])",
    re.IGNORECASE,
)

# Matches a single amount value (no leading/trailing text)
_BARE_AMOUNT_RE = re.compile(r"^[\d,]+\.\d{2}$")


def _split_narration_blocks(text: str, n: int) -> List[str]:
    """
    Split a merged narration cell into exactly n transaction description strings.

    Lines that match _NARRATION_TXNSTART_RE start a new block; all other lines
    are continuations of the previous block.  Leading continuation text (a
    cross-page narration overflow from the previous page) is discarded.
    """
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    blocks: List[List[str]] = []
    current: List[str] = []

    for line in lines:
        if _NARRATION_TXNSTART_RE.match(line):
            if current:
                blocks.append(" ".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(" ".join(current))

    # Discard leading non-transaction blocks (cross-page narration overflow)
    while blocks and not _NARRATION_TXNSTART_RE.match(blocks[0]):
        blocks = blocks[1:]

    # Pad or trim to match expected count
    if len(blocks) == n:
        return blocks
    if len(blocks) < n:
        return blocks + [""] * (n - len(blocks))
    # Too many — merge the tail into the last expected block
    split = n - 1
    return blocks[:split] + [" ".join(blocks[split:])]


def _try_expand_merged_row(row: List[str], col_map: ColumnMap) -> List[List[str]]:
    """
    If *row* has multiple newline-separated dates in the date column, it is a
    merged row.  Expand it into individual per-transaction rows.

    Returns [row] unchanged when the row is not merged (≤ 1 date found).
    """
    if col_map.date_col is None:
        return [row]

    date_cell = (row[col_map.date_col] or "").strip()
    date_strs = [v.strip() for v in date_cell.split("\n")]
    valid_dates = [s for s in date_strs if parse_date(s)]
    n = len(valid_dates)

    if n <= 1:
        return [row]  # ordinary single-transaction row

    ncols = len(row)
    skip_cols = {c for c in (col_map.date_col, col_map.desc_col) if c is not None}

    # ── Find closing-balance column ────────────────────────────────────────────
    # The rightmost column that contains exactly N bare-amount values.
    closing_bal_col: Optional[int] = None
    closing_bals: List[float] = []
    for col_idx in range(ncols - 1, -1, -1):
        if col_idx in skip_cols:
            continue
        cell = (row[col_idx] or "").strip()
        vals = [v.strip() for v in cell.split("\n") if _BARE_AMOUNT_RE.match(v.strip())]
        if len(vals) == n:
            closing_bal_col = col_idx
            closing_bals = [float(v.replace(",", "")) for v in vals]
            break

    # ── Collect withdrawal and deposit value lists ─────────────────────────────
    w_vals: List[str] = []
    d_vals: List[str] = []
    if col_map.debit_col is not None:
        cell = (row[col_map.debit_col] or "").strip()
        w_vals = [
            v.strip() for v in cell.split("\n") if _BARE_AMOUNT_RE.match(v.strip())
        ]
    if col_map.credit_col is not None:
        cell = (row[col_map.credit_col] or "").strip()
        d_vals = [
            v.strip() for v in cell.split("\n") if _BARE_AMOUNT_RE.match(v.strip())
        ]

    # ── Determine debit/credit type per transaction ───────────────────────────
    # Primary signal: closing-balance delta between consecutive transactions.
    # Fallback: assume all withdrawals when no deposit column values exist.
    txn_types: List[Optional[str]] = [None] * n

    if len(closing_bals) == n:
        for i in range(1, n):
            diff = closing_bals[i] - closing_bals[i - 1]
            txn_types[i] = "credit" if diff > 0 else "debit"

        # Resolve index-0 using remaining counts
        known_credits = txn_types.count("credit")
        remaining_credits = len(d_vals) - known_credits
        txn_types[0] = "credit" if remaining_credits > 0 else "debit"
    else:
        default = "credit" if (not w_vals and d_vals) else "debit"
        txn_types = [default] * n

    # ── Split narration into N blocks ─────────────────────────────────────────
    narrations: List[str] = [""] * n
    if col_map.desc_col is not None:
        narrations = _split_narration_blocks(row[col_map.desc_col] or "", n)

    # ── Build one row per transaction ─────────────────────────────────────────
    w_idx = d_idx = 0
    expanded: List[List[str]] = []

    for i in range(n):
        new_row = [""] * ncols
        new_row[col_map.date_col] = valid_dates[i]
        if col_map.desc_col is not None:
            new_row[col_map.desc_col] = narrations[i]

        t = txn_types[i]
        if t == "credit" and d_idx < len(d_vals):
            if col_map.credit_col is not None:
                new_row[col_map.credit_col] = d_vals[d_idx]
            elif col_map.amount_col is not None:
                new_row[col_map.amount_col] = "-" + d_vals[d_idx]
            d_idx += 1
        elif w_idx < len(w_vals):
            if col_map.debit_col is not None:
                new_row[col_map.debit_col] = w_vals[w_idx]
            elif col_map.amount_col is not None:
                new_row[col_map.amount_col] = w_vals[w_idx]
            w_idx += 1

        if closing_bal_col is not None and i < len(closing_bals):
            new_row[closing_bal_col] = str(closing_bals[i])

        expanded.append(new_row)

    return expanded


# ─── Table dispatcher ─────────────────────────────────────────────────────────

# Header keywords that identify a transaction table (vs. reward/GST/summary tables)
_TXN_HEADER_RE = re.compile(
    r"\b(date|transaction|description|narration|debit|credit|amount)\b"
    r"|withdrawal|deposit",
    re.IGNORECASE,
)


def _is_transaction_table(table: List[List[str]]) -> bool:
    """
    Return True if the table looks like a transaction table.

    Accepts both tables with a keyword header row *and* continuation pages
    that start directly with date/amount data (no repeated header).
    """
    if not table:
        return False

    header_text = " ".join(c for c in table[0] if c)
    if _TXN_HEADER_RE.search(header_text):
        return True

    # Continuation pages: first cell contains date-like values and the row
    # also contains amount-like values (e.g. bank statements with no per-page
    # header repeat).
    first_cell = (table[0][0] or "").strip() if table[0] else ""
    if _DATE_VALUE_RE.search(first_cell) and _AMOUNT_SEARCH_RE.search(header_text):
        return True

    return False


def _parse_table(
    table: List[List[str]],
    fallback_col_map: Optional[ColumnMap] = None,
) -> Tuple[List[ParsedRow], int, Optional[ColumnMap]]:
    """
    Parse a cleaned table (None cells already replaced with '') into ParsedRows.

    Args:
        table: 2-D list of strings.
        fallback_col_map: Column map from a previously parsed header row on an
            earlier page.  Used when this table has no header of its own
            (continuation pages in multi-page bank statements).

    Returns:
        (rows, skipped_count, col_map_used)
    """
    rows: List[ParsedRow] = []
    skipped = 0

    if not table:
        return rows, skipped, None

    header_idx, col_map = _find_header_row(table)

    if col_map is None:
        if fallback_col_map is not None:
            # No header on this page — reuse the col_map from the previous page
            col_map = fallback_col_map
            header_idx = None
        else:
            col_map = _detect_columns_by_heuristic(table)

    if col_map is None:
        return rows, len(table), None

    ncols = max(len(row) for row in table) if table else 1
    start_row = (header_idx + 1) if header_idx is not None else 0

    # Single-column merged format (HDFC credit card style)
    is_single_col = ncols == 1

    for row in table[start_row:]:
        if not any((c or "").strip() for c in row):
            skipped += 1
            continue

        if is_single_col:
            parsed = _parse_single_column_row((row[0] or "").strip())
            if parsed is not None:
                rows.append(parsed)
            else:
                skipped += 1
        else:
            # Attempt to expand merged rows (multiple transactions in one row)
            expanded = _try_expand_merged_row(row, col_map)
            for exp_row in expanded:
                parsed = _parse_multi_column_row(exp_row, col_map)
                if parsed is not None:
                    rows.append(parsed)
                else:
                    skipped += 1

    return rows, skipped, col_map


# ─── Main entry point ─────────────────────────────────────────────────────────


def parse_bank_statement(pdf_bytes: bytes) -> ParseResult:
    """
    Parse a bank-statement PDF supplied as raw bytes (never touches disk).

    Returns a ParseResult with:
      rows     — list of ParsedRow (txn_date, description, amount)
      skipped  — count of rows that could not be parsed
      warnings — non-fatal notes (e.g. duplicate rows removed)
    """
    result = ParseResult()
    last_col_map: Optional[ColumnMap] = None

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if not tables:
                continue

            for raw_table in tables:
                if not raw_table:
                    continue

                # Replace None with ''
                table = [[c if c is not None else "" for c in row] for row in raw_table]

                if not _is_transaction_table(table):
                    result.skipped += len(table)
                    continue

                page_rows, page_skipped, used_col_map = _parse_table(
                    table, fallback_col_map=last_col_map
                )

                # Persist the col_map so continuation pages can reuse it
                if used_col_map is not None:
                    last_col_map = used_col_map

                result.rows.extend(page_rows)
                result.skipped += page_skipped

    # ── De-duplicate exact rows (same date + description + amount) ────────────
    seen: set = set()
    unique_rows: list = []
    for row in result.rows:
        key = (row.txn_date, row.description, row.amount)
        if key not in seen:
            seen.add(key)
            unique_rows.append(row)
        else:
            result.skipped += 1
            result.warnings.append(
                f"Duplicate skipped: {row.txn_date} | "
                f"{row.description[:40]} | {row.amount}"
            )

    result.rows = unique_rows
    return result
