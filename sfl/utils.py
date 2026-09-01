"""Small stateless helpers: log-line privacy redaction, human-readable byte
sizes, and validation/normalization of the optional date and money fields
used throughout the add/edit forms and CSV import."""
import datetime
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_USERS_PATH_RE = re.compile(r"([\\/][Uu]sers[\\/])([^\\/]+)([\\/])")


def _redact_username(text: str) -> str:
    """Replace the username segment of any \\Users\\<name>\\ or
    /Users/<name>/ path with the placeholder <user>, so log lines never
    reveal who is running the app. Text with no such path is unchanged."""
    return _USERS_PATH_RE.sub(lambda m: m.group(1) + "<user>" + m.group(3), text)


def format_size(num_bytes) -> str:
    """Render a byte count as a short human string, binary units (1024).
    One decimal place at MB and up, none below that."""
    try:
        n = float(num_bytes)
    except (TypeError, ValueError):
        n = 0.0
    if n < 1024:
        return f"{int(n)} B"
    kb = n / 1024
    if kb < 1024:
        return f"{int(kb)} KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.1f} MB"
    gb = mb / 1024
    return f"{gb:.1f} GB"


# ---------------------------------------------------------------------------
# Field parsing helpers. Everything but make/model is optional, so blank
# input is always accepted and passed through as an empty string rather
# than treated as an error.
# ---------------------------------------------------------------------------
def parse_iso_date_optional(raw):
    """Validate an optional yyyy-mm-dd date. Returns ("", None) for blank
    input, (date_str, None) on success, or (None, error) on failure."""
    s = (raw or "").strip()
    if not s:
        return "", None
    try:
        datetime.date.fromisoformat(s)
    except ValueError:
        return None, "Enter a valid date."
    return s, None


def parse_decimal_optional(raw):
    """Validate an optional non-negative decimal amount, normalized to a
    plain 2-decimal string (never a float, so it never drifts). Returns
    ("", None) for blank input, or (None, error) on failure."""
    s = (raw or "").strip()
    if not s:
        return "", None
    s = s.replace("$", "").replace(",", "").strip()
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None, "Enter a valid amount."
    if not value.is_finite():
        return None, "Enter a valid amount."
    try:
        quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        if value < 0:
            return None, "Amount must be zero or greater."
        return None, "Enter a valid amount."
    if quantized < 0:
        return None, "Amount must be zero or greater."
    if quantized.is_zero():
        # Normalizes both "-0" and small negatives that round to zero (e.g.
        # "-0.001") to a plain positive zero, rather than "-0.00".
        quantized = Decimal("0.00")
    return str(quantized), None
