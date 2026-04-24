"""
Description normalisation for fuzzy matching.

Strips digits, punctuation, and noise words common in Indian bank statement
descriptions (UPI refs, merchant suffixes, channel markers, etc.) so that
rapidfuzz compares only the semantically meaningful tokens.
"""

import re

_NOISE_WORDS = frozenset(
    {
        # payment channel markers
        "upi",
        "pos",
        "neft",
        "imps",
        "rtgs",
        "nach",
        "ach",
        "bil",
        # transaction metadata tokens
        "txn",
        "ref",
        "no",
        "id",
        "cr",
        "dr",
        "mmt",
        "ybl",
        "okaxis",
        "okhdfcbank",
        "okicici",
        "oksbi",
        # UPI handle suffixes that leak through
        "pgsi",
        "www",
        # common prepositions / articles
        "to",
        "from",
        "at",
        "on",
        "the",
        "via",
        "for",
        "by",
        "of",
        "in",
        "and",
        "net",
        "banking",
        # entity suffixes
        "pvt",
        "ltd",
        "llp",
        "private",
        "limited",
        "india",
        # generic transaction verbs / category words
        "payment",
        "pay",
        "purchase",
        "transfer",
        "services",
        "service",
        "technologies",
        "technology",
        "solutions",
        "enterprises",
        "food",
        "credit",
        "card",
        # major Indian cities that appear as location suffixes
        "bangalore",
        "bengaluru",
        "mumbai",
        "delhi",
        "chennai",
        "hyderabad",
        "pune",
        "kolkata",
        "raipur",
        "ahmedabad",
        "surat",
        "jaipur",
        "lucknow",
        "kanpur",
        "nagpur",
        "indore",
        "thane",
        "bhopal",
        "visakhapatnam",
        "pimpri",
        "patna",
        "vadodara",
        "ghaziabad",
        "ludhiana",
        "agra",
        "nashik",
        "faridabad",
        "meerut",
        "rajkot",
        "varanasi",
        "srinagar",
        "aurangabad",
        "dhanbad",
        "amritsar",
        "noida",
        "gurgaon",
        "gurugram",
    }
)

# Matches payment gateway / acquirer prefixes of the form "ABC*" or "ABC123*"
# e.g. PYU*, IND*, HDFC*, SBI*, RAZORPAY*, etc.
_GATEWAY_PREFIX_RE = re.compile(r"^[A-Z0-9]+\*", re.IGNORECASE)

# Parenthetical reference blocks: "(Ref# 12345)", "(PGSI)", "(txn id 999)", etc.
_PARENS_RE = re.compile(r"\(.*?\)")

# Bare URLs
_URL_RE = re.compile(r"\b(www\S*|http\S*)\b", re.IGNORECASE)

# City names used for suffix-stripping in all-caps tokens (e.g. "BLINKITGURGAON")
_CITIES = {
    "bangalore",
    "bengaluru",
    "mumbai",
    "delhi",
    "chennai",
    "hyderabad",
    "pune",
    "kolkata",
    "raipur",
    "ahmedabad",
    "surat",
    "jaipur",
    "lucknow",
    "kanpur",
    "nagpur",
    "indore",
    "thane",
    "bhopal",
    "visakhapatnam",
    "pimpri",
    "patna",
    "vadodara",
    "ghaziabad",
    "ludhiana",
    "agra",
    "nashik",
    "faridabad",
    "meerut",
    "rajkot",
    "varanasi",
    "srinagar",
    "aurangabad",
    "dhanbad",
    "amritsar",
    "noida",
    "gurgaon",
    "gurugram",
    "newdelhi",
}

# Regex: strip a known city name from the END of a token (case-insensitive)
_CITY_SUFFIX_RE = re.compile(
    r"(" + "|".join(sorted(_CITIES, key=len, reverse=True)) + r")$",
    re.IGNORECASE,
)


def clean_description(text: str) -> str:
    """Return a human-readable cleaned description for storage and display.

    Less aggressive than normalize_description — keeps meaningful words but:
    - Strips payment gateway prefixes (PYU*, IND*, HDFC*, etc.)
    - Removes parenthetical refs like (Ref# 123456)
    - Removes bare URLs
    - Splits camelCase / ALLCAPS→TitleCase word concatenation
    - Strips known Indian city names glued to the end of tokens
      (BLINKITGURGAON → BLINKIT)
    """
    text = text.strip()

    # Strip gateway prefix: "PYU*Swiggy Food" → "Swiggy Food"
    text = _GATEWAY_PREFIX_RE.sub("", text).strip()

    # Remove parenthetical blocks: "(Ref# 00303017211556)", "(PGSI)"
    text = _PARENS_RE.sub(" ", text)

    # Remove bare URLs: "www.linkedin.com"
    text = _URL_RE.sub(" ", text)

    # Split camelCase / ALLCAPS→TitleCase concatenation
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)

    # Strip city suffix glued onto each token: "BLINKITGURGAON" → "BLINKIT"
    tokens = text.split()
    cleaned_tokens = []
    for token in tokens:
        stripped = _CITY_SUFFIX_RE.sub("", token).strip()
        # Only accept the strip if something meaningful remains
        if stripped:
            cleaned_tokens.append(stripped)
        else:
            # The whole token was a city name — drop it
            pass

    # Collapse whitespace
    text = re.sub(r"\s+", " ", " ".join(cleaned_tokens)).strip()

    return text


def normalize_description(text: str) -> str:
    """Return a normalised token string suitable for fuzzy comparison.

    Steps:
    1. Strip payment gateway prefix (anything before the first '*', e.g. 'PYU*', 'IND*')
    2. Split concatenated words at case boundaries (camelCase and ALLCAPS→TitleCase)
    3. Lowercase
    4. Replace punctuation / special chars with spaces
    5. Remove all digit sequences
    6. Split, deduplicate, and drop single-char tokens and known noise words
    7. Rejoin with single spaces
    """
    # Strip gateway prefix: "PYU*Swiggy" → "Swiggy", "IND*LINKEDIN" → "LINKEDIN"
    text = _GATEWAY_PREFIX_RE.sub("", text.strip())

    # "FoodBangalore" → "Food Bangalore"
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # "PAYMENTNet" → "PAYMENT Net", "PRIVABengaluru" → "PRIVA Bengaluru"
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)

    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)  # punctuation → space
    text = re.sub(r"\d+", " ", text)  # digits → space

    seen: set = set()
    tokens = []
    for t in text.split():
        if len(t) > 1 and t not in _NOISE_WORDS and t not in seen:
            seen.add(t)
            tokens.append(t)

    return " ".join(tokens)
