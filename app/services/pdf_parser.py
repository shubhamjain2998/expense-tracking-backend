"""
PDF bank-statement parser.

Supports four table layouts:
  1. Single-column  — HDFC credit-card style where every row is one merged text cell:
       DD/MM/YYYY| HH:MM  MERCHANT [+ N C]  C AMOUNT.XX [l]
  2. Multi-column   — separate date / description / debit / credit / amount columns,
       with one row per transaction.
  3. Merged multi-column — same 7-column layout as (2) but pdfplumber collapses all
       transactions in a page into a single row, with newline-separated values per
       cell (common in HDFC savings-account statements).  These rows are expanded
       into individual rows before parsing.
  4. Header-split   — ICICI credit-card style where each transaction is its own
       single-row table that follows a header-only table; the column map from
       the header table is reused for subsequent data tables on the same page or
       on continuation pages.

Format-specific quirks handled:
  - ICICI credit card: "Intl.# amount" header column ignored; rightmost "Amount"
    column wins; "CR" suffix → credit; EMI/Loan summary tables are skipped.
  - Paytm passbook: dates lack a year ("29 Apr 10:58 PM") — the year is inferred
    from the statement period header (e.g. "1 APR'26 - 30 APR'26"). Amounts use
    explicit "+ Rs." / "- Rs." prefixes with inverted sign convention vs. accounting
    (+ = received = credit, - = paid = debit).

Public API used by the router and the playground notebook:
  parse_bank_statement(pdf_bytes) -> ParseResult
  parse_date(s, fallback_year=None) -> Optional[date]
  parse_amount(s)                   -> Optional[float]
  _find_header_row(table)           -> (header_idx, ColumnMap | None)
  _detect_columns_by_heuristic(table) -> ColumnMap | None
  _is_transaction_table(table)      -> bool
  _parse_table(table, fallback_col_map, fallback_year) -> (rows, skipped_count, col_map)
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
    skipped_rows: List[str] = field(default_factory=list)


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

# "DD MMM" with no year — used when a statement-level fallback year is available
# (e.g. Paytm passbooks: "29 Apr", "1 Apr").
_DAY_MONTH_RE = re.compile(
    r"\b(\d{1,2})\s+" r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b",
    re.IGNORECASE,
)


def parse_date(s: str, fallback_year: Optional[int] = None) -> Optional[date]:
    """Parse a date string into a Python date.  Returns None on failure.

    If *fallback_year* is provided and the string contains a "DD MMM" pattern
    with no year (e.g. Paytm "29 Apr"), the fallback year is used to complete
    the date.
    """
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
        result = parse_date(m.group(0), fallback_year=fallback_year)
        if result is not None:
            return result
    # Year-less "DD MMM" → combine with the statement period's year
    if fallback_year is not None:
        m = _DAY_MONTH_RE.search(s)
        if m:
            day = int(m.group(1))
            mon = m.group(2).title()[:3]
            try:
                return datetime.strptime(
                    f"{day:02d} {mon} {fallback_year}", "%d %b %Y"
                ).date()
            except ValueError:
                return None
    return None


# ─── Amount parsing ───────────────────────────────────────────────────────────

_PARENS_RE = re.compile(r"^\((.+)\)$")
_CREDIT_SUFFIX_RE = re.compile(r"\s*(CR|Cr)\b")
_DEBIT_SUFFIX_RE = re.compile(r"\s*(DR|Dr)\b")
_BARE_NUMBER_RE = re.compile(r"[\d,]+\.?\d*")

# Paytm-style explicit sign: "+ Rs.25,000" (received → credit) or "- Rs.183"
# (paid → debit).  This is the inverse of the default "leading - = credit"
# convention, so it must be detected before the generic logic runs.
_PAYTM_SIGN_RE = re.compile(r"^\s*([+\-])\s*(?:Rs\.?|₹)", re.IGNORECASE)


def parse_amount(s: str) -> Optional[float]:
    """
    Parse an amount string to float.
    Positive = debit/expense, negative = credit/income.

    Handles: 1,234.56 / 1,234.56 DR / 1,234.56 CR / (500.00) / 50000CR / -
             + Rs.25,000 (Paytm credit) / - Rs.183 (Paytm debit)
    """
    if not s:
        return None
    s = s.strip()
    if s in ("-", "", "N/A", "Nil"):
        return None

    # Paytm explicit-sign convention (must run before the generic leading-`-`
    # branch, which would otherwise misclassify "- Rs.X" as a credit).
    m = _PAYTM_SIGN_RE.match(s)
    if m:
        sign = m.group(1)
        tail = s[m.end() :]  # noqa: E203
        rest = re.sub(r"[₹$€£,\s]|Rs\.?", "", tail, flags=re.IGNORECASE)
        num_m = _BARE_NUMBER_RE.search(rest)
        if not num_m:
            return None
        try:
            val = float(num_m.group(0).replace(",", ""))
        except ValueError:
            return None
        return -val if sign == "+" else val

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

# Foreign-currency / international amount columns that look like "Amount" but
# aren't the primary INR amount (e.g. ICICI's "Intl.# amount" column, which is
# blank for domestic transactions).
_FOREIGN_AMOUNT_RE = re.compile(
    r"intl\.?|international|foreign|\bfx\b|\bfcy?\b|conv(ersion)?\s*rate",
    re.IGNORECASE,
)

_DATE_VALUE_RE = re.compile(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b")
# Loose date detection used for "is this a continuation page" heuristics.
# Also accepts "DD MMM" with no year (Paytm passbook).
_DATE_LOOSE_RE = re.compile(
    r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b"
    r"|\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b",
    re.IGNORECASE,
)
_AMOUNT_VALUE_RE = re.compile(r"^\s*[\d,]+\.\d{2}\s*$")
_AMOUNT_SEARCH_RE = re.compile(r"\b[\d,]+\.\d{2}\b")
# Looser amount hint: matches "Rs.183" / "₹25,000" / "183.00" — used only to
# confirm continuation pages have transaction-shaped data on them.
_AMOUNT_HINT_RE = re.compile(
    r"(?:Rs\.?\s*[\d,]+|₹\s*[\d,]+|\b[\d,]+\.\d{2}\b)", re.IGNORECASE
)

# Headers that identify an EMI / loan summary table (not a transaction list).
# Must apply only to short header *cells* — narration cells routinely contain
# the word "installment" in transaction descriptions and would false-positive.
_EMI_LOAN_CELL_RE = re.compile(
    r"\binstallments?\b|monthly\s+installment|\bloan\s*type\b|"
    r"outstanding\s+inst|\bEMI(?:/Loan)?\s*amount\b",
    re.IGNORECASE,
)


def _looks_like_emi_summary(table: List[List[str]]) -> bool:
    """Return True if the table is a loan / EMI installment summary.

    Heuristic: at least two distinct header *cells* in the first few rows must
    match an EMI/loan marker, and each must look like a header cell (short, no
    multi-line data).  This avoids false positives on transaction narrations
    that mention "EMI" or "INSTALLMENT".
    """
    matches = 0
    for row in table[:3]:
        for cell in row:
            cell = (cell or "").strip()
            if not cell or len(cell) > 60 or "\n" in cell[: len(cell) - 1]:
                # Skip long narrations or multi-value merged cells.
                pass
            if cell and len(cell) <= 60 and _EMI_LOAN_CELL_RE.search(cell):
                matches += 1
                if matches >= 2:
                    return True
    return False


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
            # Amount: take the RIGHTMOST match, skipping any foreign-currency
            # "amount" columns (e.g. ICICI's "Intl.# amount", always blank for
            # domestic transactions).  Statements consistently place the
            # primary amount column rightmost.
            if _HDR["amount"].search(cell) and not _FOREIGN_AMOUNT_RE.search(cell):
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


def _parse_multi_column_row(
    row: List[str],
    col_map: ColumnMap,
    fallback_year: Optional[int] = None,
) -> Optional[ParsedRow]:
    """Parse one row from a standard multi-column statement table."""

    def get(col: Optional[int]) -> str:
        if col is None or col >= len(row):
            return ""
        return (row[col] or "").strip()

    txn_date = parse_date(get(col_map.date_col), fallback_year=fallback_year)
    if txn_date is None:
        return None

    desc = get(col_map.desc_col)
    if not desc:
        # Some tables share one column for date and description; fall back to
        # the date column ONLY if its content isn't itself a parseable date
        # (otherwise we'd end up with "21/05/26" as the description text,
        # which happens on HDFC-style merged rows where pdfplumber produces
        # fewer narration lines than transactions).
        candidate = get(col_map.date_col)
        if candidate and parse_date(candidate) is None:
            desc = candidate
    # Multi-line cells (Paytm "Paid to X\nUPI ID: …") collapse to a single line.
    desc = re.sub(r"\s+", " ", desc).strip()

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
        if amount is None or amount == 0:
            # 0.00 CR rows appear in some ICICI statements as EMI shadows; they
            # carry no real transaction and clutter the output.
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

    # Header keywords can live in row 0 (usual case) or row 1 (when row 0 is a
    # title cell like Paytm's "Passbook Payments History").  Scan up to 3 rows.
    scan_text = " ".join(c for row in table[:3] for c in row if c)
    if _TXN_HEADER_RE.search(scan_text):
        # Reject EMI / loan summary tables (e.g. ICICI's "Merchant EMI
        # conversions" block on the last page).  Their headers contain
        # "transaction" but they list installments, not statement entries.
        if _looks_like_emi_summary(table):
            return False
        return True

    # Continuation pages / header-split layouts (ICICI, Paytm): first cell
    # contains a date and the row carries an amount marker.
    first_cell = (table[0][0] or "").strip() if table[0] else ""
    if _DATE_LOOSE_RE.search(first_cell) and _AMOUNT_HINT_RE.search(scan_text):
        return True

    return False


def _parse_table(
    table: List[List[str]],
    fallback_col_map: Optional[ColumnMap] = None,
    fallback_year: Optional[int] = None,
) -> Tuple[List[ParsedRow], int, Optional[ColumnMap], List[str]]:
    """
    Parse a cleaned table (None cells already replaced with '') into ParsedRows.

    Args:
        table: 2-D list of strings.
        fallback_col_map: Column map from a previously parsed header row on an
            earlier page.  Used when this table has no header of its own
            (continuation pages in multi-page bank statements).
        fallback_year: Year to use when row dates are year-less (Paytm passbook).

    Returns:
        (rows, skipped_count, col_map_used, skipped_rows)
    """
    rows: List[ParsedRow] = []
    skipped = 0
    skipped_rows: List[str] = []

    if not table:
        return rows, skipped, None, skipped_rows

    header_idx, col_map = _find_header_row(table)

    if col_map is None:
        if fallback_col_map is not None:
            # No header on this page — reuse the col_map from the previous page
            col_map = fallback_col_map
            header_idx = None
        else:
            col_map = _detect_columns_by_heuristic(table)

    if col_map is None:
        for row in table:
            parts = [c.strip() for c in row if (c or "").strip()]
            skipped_rows.append(" | ".join(parts)[:120])
        return rows, len(table), None, skipped_rows

    ncols = max(len(row) for row in table) if table else 1
    start_row = (header_idx + 1) if header_idx is not None else 0

    # Single-column merged format (HDFC credit card style)
    is_single_col = ncols == 1

    for row in table[start_row:]:
        if not any((c or "").strip() for c in row):
            skipped += 1
            continue

        if is_single_col:
            cell = (row[0] or "").strip()
            parsed = _parse_single_column_row(cell)
            if parsed is not None:
                rows.append(parsed)
            else:
                skipped += 1
                summary = " ".join(cell.splitlines())[:80]
                skipped_rows.append(summary)
        else:
            # Attempt to expand merged rows (multiple transactions in one row)
            expanded = _try_expand_merged_row(row, col_map)
            for exp_row in expanded:
                parsed = _parse_multi_column_row(
                    exp_row, col_map, fallback_year=fallback_year
                )
                if parsed is not None:
                    rows.append(parsed)
                else:
                    skipped += 1
                    parts = [c.strip() for c in exp_row if (c or "").strip()]
                    skipped_rows.append(" | ".join(parts)[:120])

    return rows, skipped, col_map, skipped_rows


# ─── Generic text-line transaction extractor ──────────────────────────────────
#
# pdfplumber's extract_tables() only returns rows that are bounded by visible
# cell borders.  Many real-world statements (ICICI credit card is one example;
# others use the same pattern) draw box outlines only around highlighted rows
# and render ordinary debit lines as plain text, so those rows never appear in
# the table set at all.
#
# This module-level fallback parses each text line directly.  It is fully
# bank-agnostic: it relies only on the universal invariant that a transaction
# line contains a date and an amount, with a description in between.
#
# Disambiguation rules (kept simple on purpose):
#   • Date  — leftmost date-shaped substring on the line.
#   • Amount — the line must contain exactly ONE amount-shaped token.  Lines
#              with 2+ amounts (savings-account rows with txn + closing
#              balance) are skipped here because they're ambiguous without
#              column context; pdfplumber's table extractor handles them well
#              when borders are present.
#   • Description — text between the date and the amount, with leading
#              reference numbers and trailing reward points trimmed.

# Date patterns the line parser will recognise, including year-less "DD MMM"
# (paired with a statement-level fallback year for passbook-style PDFs).
_LINE_DATE_RE = re.compile(
    r"\b("
    r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"
    r"|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
    r"|\d{1,2}[-/.\s]+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"[a-z]*(?:[-/.,\s]+\d{2,4})?"
    r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
    r")\b",
    re.IGNORECASE,
)

# An amount token, used both to count amount candidates on a line and to
# capture the trailing transaction amount.  Accepts:
#   (1,234.56)            accounting parens
#   1,234.56 / 1234.56    decimal
#   1,234.56 CR | DR      decimal + suffix
#   Rs.1,234 / ₹500       currency with optional decimal
#   + Rs.500 / - Rs.500   currency with explicit sign (Paytm-style)
#   500 CR | DR           integer with suffix
# The leading-sign branches use look-behind to ensure we don't split a
# negative reward-points integer ("-44") off the preceding token.
_AMOUNT_TOKEN_RE = re.compile(
    r"\([\d,]+(?:\.\d{1,2})?\)"
    r"|(?<![\w])[+\-]\s*(?:Rs\.?|₹|INR)\s*[\d,]+(?:\.\d{1,2})?"
    r"|(?:Rs\.?|₹|INR)\s*[\d,]+(?:\.\d{1,2})?"
    r"|(?<![\d.,])[+\-]?\s*[\d,]+\.\d{2}\b"
    r"|(?<![\d.,])[+\-]?\s*[\d,]+(?=\s*(?:CR|DR|Cr|Dr)\b)",
)

# Suffix that may follow the amount token and changes the sign.
_AMOUNT_SUFFIX_RE = re.compile(r"\s*(CR|DR|Cr|Dr)\b")


def _parse_text_line(
    line: str, fallback_year: Optional[int] = None
) -> Optional[ParsedRow]:
    """Try to parse one raw text line as a transaction.

    Returns None if the line doesn't have exactly one date and exactly one
    amount-shaped token.  Bank-agnostic: works on any line of the universal
    form "<date> ... <description> ... <amount>[ CR|DR]".
    """
    line = line.strip()
    if not line:
        return None

    # Collect all amount tokens.  >1 means the line is ambiguous (typically a
    # savings-account row with both transaction and balance values) — defer to
    # the table extractor for those.
    amount_matches = list(_AMOUNT_TOKEN_RE.finditer(line))
    if len(amount_matches) != 1:
        return None
    amount_m = amount_matches[0]

    # Date must appear to the left of the amount.
    date_m = _LINE_DATE_RE.search(line[: amount_m.start()])
    if not date_m:
        return None

    d = parse_date(date_m.group(1), fallback_year=fallback_year)
    if d is None:
        return None

    # Include any "CR"/"DR" suffix immediately following the amount value.
    amount_end = amount_m.end()
    suffix_m = _AMOUNT_SUFFIX_RE.match(line[amount_end:])
    if suffix_m:
        amount_end += suffix_m.end()
    amount_text = line[amount_m.start() : amount_end]  # noqa: E203

    amt = parse_amount(amount_text)
    if amt is None or amt == 0:
        return None

    # Description: everything between the date and the amount.  Generic
    # cleanups only:
    #   • a leading run of digits ≥5 long is almost always a reference /
    #     serial number (ICICI SerNo, HDFC ref no., UPI ref no.).
    #   • a short trailing integer (e.g. "58", "-346") sitting just before
    #     the amount is almost always reward points / FX delta.
    desc = line[date_m.end() : amount_m.start()].strip(" \t|:-")  # noqa: E203
    desc = re.sub(r"^\d{5,}\s+", "", desc)
    desc = re.sub(r"\s+-?\d{1,4}\s*$", "", desc)
    desc = re.sub(r"\s+", " ", desc).strip()
    # Reject descriptions that are nothing but time stamps, sign markers,
    # currency glyphs, or punctuation (e.g. "23:43 + C" from PDFs that render
    # ₹ as the latin letter C).  A real merchant name has at least one short
    # alphabetic token.
    if not re.search(r"[A-Za-z]{3,}", desc):
        return None

    return ParsedRow(txn_date=d, description=desc, amount=amt)


# ─── De-duplication ──────────────────────────────────────────────────────────
#
# Two extraction sources (pdfplumber tables + raw-text lines) can both surface
# the same underlying transaction.  We can't dedupe on the exact tuple
# (date, description, amount) because the description text frequently differs
# between sources (the text-line extractor often pulls in adjacent timestamps,
# reward-point indicators, or rendering artefacts).
#
# Instead: two rows are considered the same transaction iff they share
#   • the same date,
#   • the same magnitude (sign may disagree across sources — we trust whichever
#     row was recorded first; table rows are always recorded before text-line
#     rows, so the better-signed value wins),
#   • and a long alphanumeric substring in common.

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalise_desc(s: str) -> str:
    return _NON_ALNUM_RE.sub("", s.lower())


def _descriptions_overlap(a: str, b: str, min_overlap: int = 12) -> bool:
    """True if the two descriptions reference the same merchant/payment.

    Uses a normalised-substring check: one description's alphanumeric form is
    contained in the other, OR they share a contiguous window of
    ``min_overlap`` chars.  The window threshold is intentionally generous —
    common UPI / banking artefacts ("brk@valid", "ptyes", etc.) can appear in
    unrelated transaction descriptions, so we require a substantial run of
    matching characters before declaring two rows duplicates.
    """
    na, nb = _normalise_desc(a), _normalise_desc(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short) < min_overlap:
        return False
    for i in range(len(short) - min_overlap + 1):
        if short[i : i + min_overlap] in long:  # noqa: E203
            return True
    return False


def _calendar_date(txn_date) -> date:
    """Return the calendar date for a ParsedRow.txn_date, which may be a
    plain :class:`date` (multi-column tables, text-line parser) or a
    :class:`datetime` (single-column merged tables that carry HH:MM)."""
    return txn_date.date() if isinstance(txn_date, datetime) else txn_date


def _dedupe_rows(rows: List[ParsedRow]) -> Tuple[List[ParsedRow], List[str]]:
    """Drop later rows that look like duplicates of an earlier row.

    Returns (kept_rows, warnings).  See _descriptions_overlap for the match
    criterion.
    """
    kept: List[ParsedRow] = []
    warnings: List[str] = []
    for row in rows:
        is_dup = False
        row_day = _calendar_date(row.txn_date)
        for existing in kept:
            if _calendar_date(existing.txn_date) != row_day:
                continue
            if abs(abs(existing.amount) - abs(row.amount)) > 0.005:
                continue
            if _descriptions_overlap(existing.description, row.description):
                is_dup = True
                warnings.append(
                    f"Duplicate skipped: {row.txn_date} | "
                    f"{row.description[:40]} | {row.amount}"
                )
                break
        if not is_dup:
            kept.append(row)
    return kept, warnings


# ─── Statement-level metadata helpers ─────────────────────────────────────────

# Year hints surfaced in statement-period headers.  We look for, in order:
#   1. A 4-digit year on a "From / To / Period" line
#      (e.g. "Statement period: 01/04/2026 to 30/04/2026")
#   2. A short-year "APR'26" / "Apr'26" style          (Paytm passbook)
#   3. Any standalone 20xx in the document text
_YEAR_4DIGIT_RE = re.compile(r"\b(20\d{2})\b")
_YEAR_SHORT_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*['’]\s*(\d{2})\b",
    re.IGNORECASE,
)


def _infer_statement_year(text: str) -> Optional[int]:
    """Best-effort guess at the year covered by year-less dates in a statement.

    Returns the latest year mentioned (the end of the period if a range), so
    that statements spanning a year-end pick the most recent year by default.
    """
    if not text:
        return None
    years: List[int] = []
    for m in _YEAR_4DIGIT_RE.finditer(text):
        years.append(int(m.group(1)))
    for m in _YEAR_SHORT_RE.finditer(text):
        years.append(2000 + int(m.group(1)))
    if not years:
        return None
    # Statements often print the current year (or two consecutive years) — pick
    # the most recent so a "29 Dec" date in a "Dec'25 → Jan'26" statement
    # resolves to 2025 only if the user explicitly tweaks the helper; the
    # common case (statement entirely inside one year) is unaffected.
    return max(years)


# ─── Main entry point ─────────────────────────────────────────────────────────


class PdfPasswordRequired(Exception):
    """Raised when the PDF is encrypted and no password was supplied."""


class PdfPasswordIncorrect(Exception):
    """Raised when the supplied password did not unlock the PDF."""


def parse_bank_statement(
    pdf_bytes: bytes, password: Optional[str] = None
) -> ParseResult:
    """
    Parse a bank-statement PDF supplied as raw bytes (never touches disk).

    Returns a ParseResult with:
      rows     — list of ParsedRow (txn_date, description, amount)
      skipped  — count of rows that could not be parsed
      warnings — non-fatal notes (e.g. duplicate rows removed)

    Raises:
      PdfPasswordRequired  — PDF is encrypted but `password` is None/empty
      PdfPasswordIncorrect — supplied password does not unlock the PDF
    """
    result = ParseResult()
    last_col_map: Optional[ColumnMap] = None

    # Late import so the rest of the parser keeps loading even if pdfminer's
    # internal layout shifts between versions.
    from pdfminer.pdfdocument import PDFPasswordIncorrect as _PDFPasswordIncorrect
    from pdfminer.pdfdocument import PDFEncryptionError as _PDFEncryptionError

    try:
        pdf_ctx = pdfplumber.open(io.BytesIO(pdf_bytes), password=password or "")
    except _PDFPasswordIncorrect as exc:
        # An empty password against an encrypted PDF surfaces as
        # PDFPasswordIncorrect under pdfminer — distinguish the "no password
        # supplied" case so the UI can prompt instead of saying "wrong".
        if not password:
            raise PdfPasswordRequired() from exc
        raise PdfPasswordIncorrect() from exc
    except _PDFEncryptionError as exc:
        raise PdfPasswordRequired() from exc

    with pdf_ctx as pdf:
        # Infer the statement year from the first page's text so that
        # year-less dates (Paytm "29 Apr") can still be parsed.
        try:
            first_page_text = pdf.pages[0].extract_text() if pdf.pages else ""
        except Exception:
            first_page_text = ""
        fallback_year = _infer_statement_year(first_page_text or "")

        for page in pdf.pages:
            # ── 1. Table-based extraction (good when cell borders are present)
            tables = page.extract_tables()
            for raw_table in tables or []:
                if not raw_table:
                    continue

                # Replace None with ''
                table = [[c if c is not None else "" for c in row] for row in raw_table]

                if not _is_transaction_table(table):
                    result.skipped += len(table)
                    for row in table:
                        parts = [c.strip() for c in row if (c or "").strip()]
                        if parts:
                            result.skipped_rows.append(" | ".join(parts)[:120])
                    continue

                page_rows, page_skipped, used_col_map, page_skipped_rows = _parse_table(
                    table,
                    fallback_col_map=last_col_map,
                    fallback_year=fallback_year,
                )

                # Persist the col_map so continuation pages can reuse it
                if used_col_map is not None:
                    last_col_map = used_col_map

                result.rows.extend(page_rows)
                result.skipped += page_skipped
                result.skipped_rows.extend(page_skipped_rows)

            # ── 2. Generic text-line extraction (catches rows that have no
            #      visible cell border and so are absent from extract_tables).
            #      Bank-agnostic: the parser only requires one date and one
            #      unambiguous amount per line.  Overlap with the table rows
            #      above is removed by the smart deduper below.
            try:
                page_text = page.extract_text() or ""
            except Exception:
                page_text = ""
            for line in page_text.splitlines():
                parsed = _parse_text_line(line, fallback_year=fallback_year)
                if parsed is not None:
                    result.rows.append(parsed)

    # ── 3. Smart de-duplication
    #      A row from the text-line scan that matches a table-extracted row
    #      (same date, same |amount|, overlapping description) is dropped in
    #      favour of the earlier (table-extracted) row, which generally has
    #      cleaner whitespace and the correct sign.  Duplicates are recorded
    #      as warnings rather than counted against ``skipped`` (which the API
    #      surfaces as "rows pdfplumber could not parse" — duplicate-removal
    #      is a different concept).
    result.rows, dup_warnings = _dedupe_rows(result.rows)
    result.warnings.extend(dup_warnings)
    return result
