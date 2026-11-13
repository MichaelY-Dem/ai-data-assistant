"""Small, pure functions that turn messy text into clean typed values.

Each parser returns None for a missing value and raises ValueError when the text
can't be interpreted. clean_and_load.py decides what to do with that (set NULL,
drop the row) and logs it. Keeping these pure makes them easy to unit-test.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Callable

MISSING_TOKENS = {"", "n/a", "na", "null", "none", "-", "--", "?", "tbd", "undisclosed", "unknown"}


def is_missing(value: str | None) -> bool:
    return value is None or str(value).strip().lower() in MISSING_TOKENS


def is_plain_number(text: str) -> bool:
    """True for text that's already a bare number like 123 or -45.60."""
    return re.fullmatch(r"-?\d+(\.\d+)?", text) is not None


# --- text -------------------------------------------------------------------

def clean_text(value: str | None) -> str | None:
    """Trim and collapse internal whitespace."""
    if is_missing(value):
        return None
    return re.sub(r"\s+", " ", str(value)).strip()


_KEEP_UPPER = {"Plc": "PLC", "Llc": "LLC", "Ag": "AG", "Sa": "SA", "Ftth": "FTTH"}


def clean_name(value: str | None) -> str | None:
    """Like clean_text, and converts SHOUTED NAMES to Title Case."""
    text = clean_text(value)
    if text and text.isupper() and len(text) > 4:
        text = " ".join(_KEEP_UPPER.get(w, w) for w in text.title().split())
    return text


def normalize_key(value: str) -> str:
    """'  Toll-Road ' -> 'toll road'. Used to match against alias tables."""
    return re.sub(r"[\s_\-]+", " ", value.strip().lower()).strip()


def make_lookup(aliases: dict[str, str]) -> Callable[[str | None], str | None]:
    """Build a parser that maps any known spelling to its canonical value."""
    table = {normalize_key(k): v for k, v in aliases.items()}

    def lookup(value: str | None) -> str | None:
        if is_missing(value):
            return None
        try:
            return table[normalize_key(str(value))]
        except KeyError:
            raise ValueError(f"unknown value {value!r}") from None

    return lookup


# --- identifiers ----------------------------------------------------------------

def normalize_company_code(value: str | None) -> str:
    """'co-7', 'CO7', ' CO-007 ', 'co 007' -> 'CO-007'."""
    match = re.fullmatch(r"co[\s\-_]*0*(\d{1,3})", (value or "").strip(), flags=re.IGNORECASE)
    if not match:
        raise ValueError(f"invalid company code {value!r}")
    return f"CO-{int(match.group(1)):03d}"


# --- dates ------------------------------------------------------------------------

# Convention (documented in docs/data-cleaning.md): slashes with the year LAST are
# day-first (European exports); dashes with the year last are month-first (US exports).
DATE_FORMATS = [
    "%Y-%m-%d", "%Y/%m/%d",
    "%d/%m/%Y",
    "%m-%d-%Y",
    "%B %d, %Y", "%b %d, %Y",
    "%d-%b-%Y", "%d %B %Y", "%d %b %Y",
]
EXCEL_EPOCH = date(1899, 12, 30)


def parse_date(value: str | None) -> date | None:
    if is_missing(value):
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{5}", text):  # Excel serial number, e.g. 44562
        serial = int(text)
        if 20000 <= serial <= 80000:  # roughly 1954-2119
            return EXCEL_EPOCH + timedelta(days=serial)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {value!r}")


# --- numbers --------------------------------------------------------------------

def parse_number(value: str | None) -> float | None:
    """'$1,234.50', 'USD 12', '(45.2)' (accounting negative), '-$3.1' -> float."""
    if is_missing(value):
        return None
    text = str(value).strip().lower()
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    text = re.sub(r"us\$|usd|\$|,|\s", "", text)
    if text.startswith("-"):
        negative, text = not negative, text[1:]
    number = float(text)  # raises ValueError for anything left that isn't numeric
    return -number if negative else number


FULL_DOLLAR_THRESHOLD = 100_000  # no single figure in this portfolio is $100bn+


def parse_money_musd(value: str | None) -> float | None:
    """Money in millions of USD. Understands '1.2bn', '450m', 'USD 450 million',
    and full-dollar figures like '450000000' (converted to 450.0)."""
    if is_missing(value):
        return None
    text = str(value).strip().lower()
    multiplier = 1.0
    match = re.fullmatch(r"(.*?)\s*(bn|billion|b|mm|million|m)", text)
    if match:
        text = match.group(1)
        multiplier = 1000.0 if match.group(2) in ("bn", "billion", "b") else 1.0
    number = parse_number(text)
    if number is None:
        return None
    number *= multiplier
    if not match and abs(number) >= FULL_DOLLAR_THRESHOLD:
        number /= 1_000_000
    return round(number, 2)


def parse_pct(value: str | None) -> float | None:
    """'45%', '45 %', '45' -> 45.0. A bare value <= 1 with no % sign is read as a
    fraction (0.455 -> 45.5), because no stake in this portfolio is below 1%."""
    if is_missing(value):
        return None
    text = str(value)
    had_percent_sign = "%" in text
    number = parse_number(text.replace("%", ""))
    if number is None:
        return None
    if not had_percent_sign and 0 < number <= 1:
        number *= 100
    return round(number, 2)


def parse_year(value: str | None) -> int | None:
    """'2023', '2023.0', 'est. 1998', 'FY2023', 'FY23' -> int."""
    if is_missing(value):
        return None
    text = str(value).strip()
    match = re.search(r"(?<!\d)(\d{4})(?!\d)", text)
    if match:
        return int(match.group(1))
    match = re.fullmatch(r"fy\s*'?(\d{2})", text, flags=re.IGNORECASE)
    if match:
        return 2000 + int(match.group(1))
    raise ValueError(f"unrecognised year {value!r}")


UNIT_ALIASES = {"mw": "MW", "km": "km", "ml/day": "ML/day", "ml/d": "ML/day", "mld": "ML/day"}


def parse_unit(value: str | None) -> str | None:
    if is_missing(value):
        return None
    try:
        return UNIT_ALIASES[str(value).strip().lower()]
    except KeyError:
        raise ValueError(f"unknown unit {value!r}") from None


def parse_capacity(value: str | None) -> tuple[float | None, str | None]:
    """'250 MW', '250MW', '1,200' -> (value, unit-or-None)."""
    if is_missing(value):
        return None, None
    match = re.fullmatch(r"\s*([\d,]*\.?\d+)\s*([A-Za-z/]+)?\s*", str(value))
    if not match:
        raise ValueError(f"unrecognised capacity {value!r}")
    number = parse_number(match.group(1))
    unit = parse_unit(match.group(2)) if match.group(2) else None
    return number, unit
