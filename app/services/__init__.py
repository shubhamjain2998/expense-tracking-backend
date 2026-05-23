"""Pure-Python services — parsing, normalisation, and date math.

These modules contain no HTTP or ORM logic; they take simple inputs (bytes,
strings, dates) and return simple outputs (lists of tuples, parsed rows). The
routers compose them with database I/O.

    pdf_parser  — extract (date, description, amount) rows from a bank PDF
    text_parser — same, for pasted statement text
    normalizer  — strip noise (UPI refs, channel markers) before fuzzy matching
    period      — calendar ↔ Indian financial year date helpers
    backup      — serialise / deserialise a user's full data set as JSON
"""
