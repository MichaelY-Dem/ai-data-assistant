"""Clean and validate the raw portfolio CSVs, log every fix, and load MySQL.

Pipeline (every change is recorded in docs/data-cleaning.md):
  1. Read data/raw/*.csv
  2. Drop fully blank rows and exact duplicate rows
  3. Parse and normalise every field (dates, numbers, percentages, aliases, whitespace)
  4. Drop rows that point at a company that doesn't exist (orphans)
  5. Merge duplicates that share a business key (e.g. same company code + year)
  6. Fill gaps from other fields (sector from subsector, country from city, ...)
  7. Drop rows still missing a required field
  8. Validate ranges and business rules (impossible -> NULL, suspicious -> warning)
  9. Write data/clean/*.csv and docs/data-cleaning.md
 10. Create the `portfolio` database if needed, recreate the tables, insert everything

Usage:
    python data/clean_and_load.py              # clean + load into MySQL
    python data/clean_and_load.py --skip-load  # clean only (no database needed)
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

import cleaning_rules as cr
from reference import (ASSET_TYPE_TO_UNIT, CITY_TO_COUNTRY, COMPANY_STATUS_ALIASES,
                       COUNTRY_ALIASES, DEAL_STATUS_ALIASES, DEAL_TYPE_ALIASES,
                       SECTOR_ALIASES, SECTORS, SUBSECTOR_TO_SECTOR)

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
RAW_DIR = HERE / "raw"
CLEAN_DIR = HERE / "clean"
SCHEMA_PATH = HERE / "schema.sql"
LOG_PATH = PROJECT_ROOT / "docs" / "data-cleaning.md"
TODAY = date.today()
TABLES = ["companies", "financials", "assets", "deals"]

COLUMNS = {
    "companies": ["company_code", "name", "sector", "subsector", "country", "hq_city",
                  "founded_year", "ownership_pct", "acquisition_date", "status"],
    "financials": ["company_code", "fiscal_year", "revenue_musd", "ebitda_musd",
                   "net_debt_musd", "capex_musd"],
    "assets": ["company_code", "asset_name", "asset_type", "country", "city", "capacity_value",
               "capacity_unit", "commissioning_date", "book_value_musd"],
    "deals": ["company_code", "deal_type", "deal_date", "deal_value_musd", "equity_stake_pct",
              "counterparty", "status"],
}
REQUIRED = {
    "companies": ["name", "sector", "status"],
    "financials": ["fiscal_year"],
    "assets": ["asset_name", "asset_type"],
    "deals": ["deal_type", "status"],
}
NO_STAKE_DEALS = {"follow_on", "refinancing"}

# Alias lookups: messy spelling -> canonical value (raise ValueError if unknown)
to_sector = cr.make_lookup(SECTOR_ALIASES)
to_country = cr.make_lookup(COUNTRY_ALIASES)
to_company_status = cr.make_lookup(COMPANY_STATUS_ALIASES)
to_deal_type = cr.make_lookup(DEAL_TYPE_ALIASES)
to_deal_status = cr.make_lookup(DEAL_STATUS_ALIASES)
to_subsector = cr.make_lookup({s: s for info in SECTORS.values() for s in info["subsectors"]})
to_asset_type = cr.make_lookup({t: t for info in SECTORS.values() for t in info["subsectors"].values()})

# Rule names (grouped in the summary table of the log)
R_CODE = "Company code reformatted to CO-NNN"
R_TEXT = "Whitespace trimmed/collapsed"
R_NAME = "Name whitespace/capitalisation fixed"
R_ALIAS = "Spelling variant mapped to canonical value"
R_YEAR = "Year stored as text converted to integer"
R_NUMBER = "Number stored as text converted (USD millions)"
R_PCT = "Percentage text/fraction converted to 0-100"
R_DATE = "Date converted to YYYY-MM-DD"
R_UNPARSEABLE = "Unparseable value set to NULL"
R_PLACEHOLDER = "Placeholder text (N/A, -, ...) set to NULL"
R_BLANK = "Blank value stored as NULL"
R_FILLED_DUP = "Blank filled from a duplicate row"
R_CASE_DUP = "Proper capitalisation taken from a duplicate row"
R_INFER = "Missing value inferred from related field"
R_UNIT_SPLIT = "Unit split out of capacity text"
R_RANGE = "Out-of-range value set to NULL"
R_BACKFILL = "Acquisition date filled from the acquisition deal"


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    kind: str  # "fix" | "removed" | "warning"
    table: str
    record: str
    column: str
    original: object
    cleaned: object
    rule: str
    detail: str = ""


class CleaningLog:
    def __init__(self) -> None:
        self.entries: list[Entry] = []
        self._touched: set[tuple[str, str, str]] = set()

    def fix(self, table, record, column, original, cleaned, rule, detail=""):
        self.entries.append(Entry("fix", table, record, column, original, cleaned, rule, detail))
        self._touched.add((table, record, column))

    def removed(self, table, record, rule, detail="", original=""):
        self.entries.append(Entry("removed", table, record, "(row)", original, None, rule, detail))

    def warn(self, table, record, column, value, rule, detail=""):
        self.entries.append(Entry("warning", table, record, column, value, value, rule, detail))

    def touched(self, table, record, column) -> bool:
        return (table, record, column) in self._touched


def is_canonical(raw: str, value) -> bool:
    """Was the raw text already in the form we'd store? If so it's not a 'fix'."""
    if isinstance(value, date):
        return raw == value.isoformat()
    if isinstance(value, int):
        return raw == str(value)
    if isinstance(value, float):
        return cr.is_plain_number(raw) and float(raw) == value
    return raw == value


class RowCleaner:
    """Parses one raw row's fields, logging every value that had to change."""

    def __init__(self, log: CleaningLog, table: str, record: str):
        self.log, self.table, self.record = log, table, record

    def field(self, column: str, raw: str | None, parser: Callable, rule: str):
        raw = raw if raw is not None else ""
        try:
            value = parser(raw)
        except ValueError as exc:
            self.log.fix(self.table, self.record, column, raw, None, R_UNPARSEABLE, str(exc))
            return None
        if value is None:
            if raw.strip():
                self.log.fix(self.table, self.record, column, raw, None, R_PLACEHOLDER)
            return None
        if not is_canonical(raw, value):
            self.log.fix(self.table, self.record, column, raw, value, rule)
        return value


# ---------------------------------------------------------------------------
# Row parsers (one per table)
# ---------------------------------------------------------------------------

def parse_company(raw: dict, rc: RowCleaner) -> dict:
    return {
        "name": rc.field("name", raw["name"], cr.clean_name, R_NAME),
        "sector": rc.field("sector", raw["sector"], to_sector, R_ALIAS),
        "subsector": rc.field("subsector", raw["subsector"], to_subsector, R_ALIAS),
        "country": rc.field("country", raw["country"], to_country, R_ALIAS),
        "hq_city": rc.field("hq_city", raw["hq_city"], cr.clean_text, R_TEXT),
        "founded_year": rc.field("founded_year", raw["founded_year"], cr.parse_year, R_YEAR),
        "ownership_pct": rc.field("ownership_pct", raw["ownership_pct"], cr.parse_pct, R_PCT),
        "acquisition_date": rc.field("acquisition_date", raw["acquisition_date"], cr.parse_date, R_DATE),
        "status": rc.field("status", raw["status"], to_company_status, R_ALIAS),
    }


def parse_financial(raw: dict, rc: RowCleaner) -> dict:
    rec = {"fiscal_year": rc.field("fiscal_year", raw["fiscal_year"], cr.parse_year, R_YEAR)}
    for col in ("revenue_musd", "ebitda_musd", "net_debt_musd", "capex_musd"):
        rec[col] = rc.field(col, raw[col], cr.parse_money_musd, R_NUMBER)
    return rec


def parse_asset(raw: dict, rc: RowCleaner) -> dict:
    rec = {
        "asset_name": rc.field("asset_name", raw["asset_name"], cr.clean_text, R_TEXT),
        "asset_type": rc.field("asset_type", raw["asset_type"], to_asset_type, R_ALIAS),
        "country": rc.field("country", raw["country"], to_country, R_ALIAS),
        "city": rc.field("city", raw["city"], cr.clean_text, R_TEXT),
        "commissioning_date": rc.field("commissioning_date", raw["commissioning_date"], cr.parse_date, R_DATE),
        "book_value_musd": rc.field("book_value_musd", raw["book_value_musd"], cr.parse_money_musd, R_NUMBER),
    }
    # Capacity sometimes arrives as "250 MW" with the unit column blank
    unit_col = rc.field("capacity_unit", raw["capacity_unit"], cr.parse_unit, R_ALIAS)
    cap_raw = raw["capacity"] or ""
    try:
        value, unit_in_text = cr.parse_capacity(cap_raw)
    except ValueError as exc:
        rc.log.fix(rc.table, rc.record, "capacity_value", cap_raw, None, R_UNPARSEABLE, str(exc))
        value, unit_in_text = None, None
    if value is not None and not is_canonical(cap_raw, value):
        rc.log.fix(rc.table, rc.record, "capacity_value", cap_raw, value,
                   R_UNIT_SPLIT if unit_in_text else R_NUMBER)
    if unit_col is None and unit_in_text:
        rc.log.fix(rc.table, rc.record, "capacity_unit", raw["capacity_unit"], unit_in_text, R_UNIT_SPLIT)
    elif unit_col and unit_in_text and unit_col != unit_in_text:
        rc.log.warn(rc.table, rc.record, "capacity_unit", unit_col, "Unit column disagrees with capacity text",
                    f"capacity text says {unit_in_text}; kept {unit_col}")
    rec["capacity_value"] = value
    rec["capacity_unit"] = unit_col or unit_in_text
    return rec


def parse_deal(raw: dict, rc: RowCleaner) -> dict:
    return {
        "deal_type": rc.field("deal_type", raw["deal_type"], to_deal_type, R_ALIAS),
        "deal_date": rc.field("deal_date", raw["deal_date"], cr.parse_date, R_DATE),
        "deal_value_musd": rc.field("deal_value_musd", raw["deal_value_musd"], cr.parse_money_musd, R_NUMBER),
        "equity_stake_pct": rc.field("equity_stake_pct", raw["equity_stake_pct"], cr.parse_pct, R_PCT),
        "counterparty": rc.field("counterparty", raw["counterparty"], cr.clean_text, R_TEXT),
        "status": rc.field("status", raw["status"], to_deal_status, R_ALIAS),
    }


# ---------------------------------------------------------------------------
# Gap filling (inference) and validation
# ---------------------------------------------------------------------------

def infer_company(r: dict, log: CleaningLog) -> None:
    if r["sector"] is None and r["subsector"]:
        r["sector"] = SUBSECTOR_TO_SECTOR[r["subsector"].lower()]
        log.fix("companies", r["_id"], "sector", None, r["sector"], R_INFER, f"from subsector {r['subsector']!r}")
    if r["country"] is None and r["hq_city"] and r["hq_city"].lower() in CITY_TO_COUNTRY:
        r["country"] = CITY_TO_COUNTRY[r["hq_city"].lower()]
        log.fix("companies", r["_id"], "country", None, r["country"], R_INFER, f"from hq_city {r['hq_city']!r}")


def infer_asset(r: dict, log: CleaningLog) -> None:
    if r["capacity_unit"] is None and r["capacity_value"] is not None and r["asset_type"]:
        r["capacity_unit"] = ASSET_TYPE_TO_UNIT[r["asset_type"].lower()]
        log.fix("assets", r["_id"], "capacity_unit", None, r["capacity_unit"], R_INFER,
                f"from asset_type {r['asset_type']!r}")
    if r["country"] is None and r["city"] and r["city"].lower() in CITY_TO_COUNTRY:
        r["country"] = CITY_TO_COUNTRY[r["city"].lower()]
        log.fix("assets", r["_id"], "country", None, r["country"], R_INFER, f"from city {r['city']!r}")


def null_if_outside(r: dict, col: str, lo: float, hi: float, table: str, log: CleaningLog) -> None:
    v = r[col]
    if v is not None and not (lo <= v <= hi):
        log.fix(table, r["_id"], col, v, None, R_RANGE, f"allowed range {lo}–{hi}")
        r[col] = None


def validate_company(r: dict, log: CleaningLog) -> bool:
    null_if_outside(r, "founded_year", 1800, TODAY.year, "companies", log)
    null_if_outside(r, "ownership_pct", 0.01, 100, "companies", log)
    if r["acquisition_date"] and r["acquisition_date"] > TODAY:
        log.fix("companies", r["_id"], "acquisition_date", r["acquisition_date"], None, R_RANGE, "date is in the future")
        r["acquisition_date"] = None
    return True


def validate_financial(r: dict, log: CleaningLog) -> bool:
    if not 2000 <= r["fiscal_year"] <= TODAY.year:
        log.removed("financials", r["_id"], "Fiscal year out of range", f"{r['fiscal_year']}")
        return False
    null_if_outside(r, "revenue_musd", 0, 1_000_000, "financials", log)
    null_if_outside(r, "capex_musd", 0, 1_000_000, "financials", log)
    if r["revenue_musd"] is not None and r["ebitda_musd"] is not None and r["ebitda_musd"] > r["revenue_musd"]:
        log.warn("financials", r["_id"], "ebitda_musd", r["ebitda_musd"], "EBITDA exceeds revenue",
                 f"revenue {r['revenue_musd']}")
    return True


def validate_asset(r: dict, log: CleaningLog) -> bool:
    null_if_outside(r, "capacity_value", 0.01, 1_000_000, "assets", log)
    null_if_outside(r, "book_value_musd", 0, 1_000_000, "assets", log)
    if r["commissioning_date"] and r["commissioning_date"] > TODAY:
        log.warn("assets", r["_id"], "commissioning_date", r["commissioning_date"],
                 "Commissioning date in the future", "kept; assumed under construction")
    return True


def validate_deal(r: dict, log: CleaningLog) -> bool:
    null_if_outside(r, "equity_stake_pct", 0.01, 100, "deals", log)
    null_if_outside(r, "deal_value_musd", 0, 1_000_000, "deals", log)
    if r["status"] == "closed" and r["deal_date"] and r["deal_date"] > TODAY:
        log.warn("deals", r["_id"], "deal_date", r["deal_date"], "Closed deal dated in the future")
    return True


def cross_table_checks(companies: list[dict], deals: list[dict], log: CleaningLog) -> None:
    entry_dates: dict[str, date] = {}
    for d in deals:
        if d["deal_type"] == "acquisition" and d["status"] == "closed" and d["deal_date"]:
            code = d["company_code"]
            entry_dates[code] = min(d["deal_date"], entry_dates.get(code, d["deal_date"]))
    for c in companies:
        deal_date = entry_dates.get(c["company_code"])
        if c["acquisition_date"] is None and deal_date:
            c["acquisition_date"] = deal_date
            log.fix("companies", c["_id"], "acquisition_date", None, deal_date, R_BACKFILL)
        elif c["acquisition_date"] and deal_date and c["acquisition_date"] != deal_date:
            log.warn("companies", c["_id"], "acquisition_date", c["acquisition_date"],
                     "Acquisition date differs from acquisition deal", f"deal date {deal_date}")
        if c["acquisition_date"] and c["founded_year"] and c["acquisition_date"].year < c["founded_year"]:
            log.warn("companies", c["_id"], "founded_year", c["founded_year"],
                     "Acquired before the company was founded", f"acquired {c['acquisition_date']}")


# ---------------------------------------------------------------------------
# Generic table pipeline
# ---------------------------------------------------------------------------

def drop_blank_and_exact_duplicates(rows: list[dict], table: str, log: CleaningLog) -> list[dict]:
    seen: dict[tuple, int] = {}
    out = []
    for r in rows:
        values = tuple(v for k, v in r.items() if k != "_line")
        if all(cr.is_missing(v) for v in values):
            log.removed(table, f"line {r['_line']}", "Completely blank row")
            continue
        if values in seen:
            log.removed(table, f"line {r['_line']}", "Exact duplicate row", f"same as line {seen[values]}")
            continue
        seen[values] = r["_line"]
        out.append(r)
    return out


def dedupe(records: list[dict], key_cols: list[str], table: str, log: CleaningLog) -> list[dict]:
    """Merge records sharing a business key. Keep the most complete one (first wins
    a tie), fill its blanks from the others, and flag real conflicts for review."""
    groups: dict[tuple, list[dict]] = {}
    for r in records:
        groups.setdefault(tuple(r[c] for c in key_cols), []).append(r)
    out = []
    for key, group in groups.items():
        base = max(group, key=lambda r: sum(v is not None for k, v in r.items() if not k.startswith("_")))
        for other in group:
            if other is base:
                continue
            for col, value in other.items():
                if col.startswith("_") or value is None:
                    continue
                if base[col] is None:
                    base[col] = value
                    log.fix(table, base["_id"], col, None, value, R_FILLED_DUP, f"from {other['_id']}")
                elif isinstance(value, str) and str(base[col]).casefold() == value.casefold():
                    # Same text, different case: prefer the properly cased spelling
                    if base[col] in (base[col].lower(), base[col].upper()) and value not in (value.lower(), value.upper()):
                        log.fix(table, base["_id"], col, base[col], value, R_CASE_DUP, f"from {other['_id']}")
                        base[col] = value
                elif base[col] != value:
                    log.warn(table, base["_id"], col, base[col], "Duplicates disagree; kept the more complete row",
                             f"{other['_id']} had {fmt_plain(value)}")
            log.removed(table, other["_id"], "Duplicate merged into another row",
                        f"same {'/'.join(key_cols)} as {base['_id']}")
        out.append(base)
    return out


def run_table(table: str, raw_rows: list[dict], row_parser: Callable, key_cols: list[str],
              log: CleaningLog, known_codes: set[str] | None = None,
              infer: Callable | None = None, validate: Callable | None = None) -> list[dict]:
    parsed = []
    for raw in drop_blank_and_exact_duplicates(raw_rows, table, log):
        try:
            code = cr.normalize_company_code(raw["company_code"])
        except ValueError:
            log.removed(table, f"line {raw['_line']}", "Missing or invalid company_code", original=raw["company_code"])
            continue
        record = f"line {raw['_line']} ({code})"
        if known_codes is not None and code not in known_codes:
            log.removed(table, record, "Orphan row: company does not exist", original=raw["company_code"])
            continue
        rc = RowCleaner(log, table, record)
        rec = {"_id": record,
               "company_code": rc.field("company_code", raw["company_code"], cr.normalize_company_code, R_CODE)}
        rec.update(row_parser(raw, rc))
        parsed.append(rec)

    records = dedupe(parsed, key_cols, table, log)
    kept = []
    for r in records:
        if infer:
            infer(r, log)
        missing = [c for c in REQUIRED[table] if r.get(c) is None]
        if missing:
            log.removed(table, r["_id"], "Missing required field", ", ".join(missing))
            continue
        if validate and not validate(r, log):
            continue
        kept.append(r)
    return kept


def clean_all(raw: dict[str, list[dict]]) -> tuple[dict[str, list[dict]], CleaningLog]:
    """Clean all four tables. `raw` maps table -> list of dicts of strings."""
    raw = {t: [{**r, "_line": r.get("_line", i)} for i, r in enumerate(rows, start=2)]
           for t, rows in raw.items()}
    log = CleaningLog()
    companies = run_table("companies", raw["companies"], parse_company, ["company_code"], log,
                          infer=infer_company, validate=validate_company)
    codes = {c["company_code"] for c in companies}
    financials = run_table("financials", raw["financials"], parse_financial,
                           ["company_code", "fiscal_year"], log, codes, validate=validate_financial)
    assets = run_table("assets", raw["assets"], parse_asset, ["company_code", "asset_name"], log,
                       codes, infer=infer_asset, validate=validate_asset)
    deals = run_table("deals", raw["deals"], parse_deal, ["company_code", "deal_type", "deal_date"],
                      log, codes, validate=validate_deal)
    cross_table_checks(companies, deals, log)

    clean = {"companies": companies, "financials": financials, "assets": assets, "deals": deals}
    for table, rows in clean.items():  # anything still empty and not yet explained
        for r in rows:
            for col in COLUMNS[table]:
                if (table, col) == ("deals", "equity_stake_pct") and r["deal_type"] in NO_STAKE_DEALS:
                    continue  # a stake doesn't apply to these deals, so blank is correct
                if r[col] is None and not log.touched(table, r["_id"], col):
                    log.fix(table, r["_id"], col, "", None, R_BLANK)
    return clean, log


# ---------------------------------------------------------------------------
# Output: CSVs and Markdown log
# ---------------------------------------------------------------------------

def fmt_plain(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


def fmt_md(v) -> str:
    if v is None:
        return "*NULL*"
    s = fmt_plain(v)
    if s == "":
        return "*(blank)*"
    s = s.replace("|", "\\|")
    if s != s.strip() or "  " in s:
        return f'`"{s}"`'  # quotes make stray spaces visible
    return f"`{s}`"


def read_raw(raw_dir: Path = RAW_DIR) -> dict[str, list[dict]]:
    raw = {}
    for table in TABLES:
        path = raw_dir / f"{table}.csv"
        if not path.exists():
            raise SystemExit(f"{path} not found. Run: python data/generate_fake_data.py")
        with open(path, newline="", encoding="utf-8-sig") as fh:
            raw[table] = [{**row, "_line": i} for i, row in enumerate(csv.DictReader(fh), start=2)]
    return raw


def write_clean_csvs(clean: dict[str, list[dict]], out_dir: Path = CLEAN_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for table, rows in clean.items():
        with open(out_dir / f"{table}.csv", "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(COLUMNS[table])
            for r in rows:
                writer.writerow(["" if r[c] is None else fmt_plain(r[c]) for c in COLUMNS[table]])


ASSUMPTIONS = """\
- **Units.** All money is stored in millions of US dollars. `1.2bn` → 1200, `450m` / `USD 450 million` → 450.
  A bare figure of 100,000 or more is assumed to be whole dollars and divided by 1,000,000.
- **Accounting negatives.** `(12.50)` means -12.50.
- **Dates.** Slashes with the year last (`14/03/2019`) are read **day-first** (European export); dashes with
  the year last (`03-14-2019`) are read **month-first** (US export). Five-digit numbers (`43538`) are Excel
  serial dates. Anything else that doesn't parse (e.g. 31 February) becomes NULL.
- **Percentages.** `45%`, `45 %` and `45` all mean 45. A bare value of 1 or less with no `%` (`0.455`) is a
  fraction and is multiplied by 100.
- **Duplicates.** Rows identical in every column are dropped. Rows sharing a business key (company code;
  company + fiscal year; company + asset name; company + deal type + date) are merged: the most complete row
  wins (the earlier one on a tie), its blanks are filled from the others, and any disagreement is listed under
  *Warnings* for a human to check.
- **Gap filling.** Missing sector comes from subsector, missing country from city, missing capacity unit from
  asset type, missing acquisition date from the company's closed acquisition deal.
- **Orphans.** Financials, assets or deals for a company code that isn't in `companies` are dropped.
"""


def write_log(log: CleaningLog, raw: dict[str, list[dict]], clean: dict[str, list[dict]],
              path: Path = LOG_PATH) -> None:
    by_kind = Counter((e.table, e.kind) for e in log.entries)
    lines = [
        "# Data cleaning log",
        "",
        f"_Generated by `data/clean_and_load.py` on {datetime.now():%Y-%m-%d %H:%M}. "
        "Do not edit by hand: rerun the script to regenerate it._",
        "",
        "## Summary",
        "",
        "| Table | Raw rows | Clean rows | Value fixes | Rows removed | Warnings |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for t in TABLES:
        lines.append(f"| {t} | {len(raw[t])} | {len(clean[t])} | {by_kind[(t, 'fix')]} | "
                     f"{by_kind[(t, 'removed')]} | {by_kind[(t, 'warning')]} |")

    lines += ["", "### Fixes by rule", "", "| Table | Rule | Count |", "|---|---|---:|"]
    for (t, rule), n in sorted(Counter((e.table, e.rule) for e in log.entries if e.kind == "fix").items(),
                               key=lambda kv: (TABLES.index(kv[0][0]), -kv[1])):
        lines.append(f"| {t} | {rule} | {n} |")

    lines += ["", "## Assumptions", "", ASSUMPTIONS]

    removed = [e for e in log.entries if e.kind == "removed"]
    lines += ["## Rows removed", "", "| Table | Record | Reason | Detail |", "|---|---|---|---|"]
    lines += [f"| {e.table} | {e.record} | {e.rule} | {e.detail} |" for e in removed]

    warnings = [e for e in log.entries if e.kind == "warning"]
    lines += ["", "## Warnings (need a human to review)", "",
              "| Table | Record | Column | Value kept | Issue | Detail |", "|---|---|---|---|---|---|"]
    lines += [f"| {e.table} | {e.record} | {e.column} | {fmt_md(e.original)} | {e.rule} | {e.detail} |"
              for e in warnings]

    lines += ["", "## Every value fix", ""]
    for t in TABLES:
        fixes = [e for e in log.entries if e.kind == "fix" and e.table == t]
        lines += [f"### {t} ({len(fixes)})", "",
                  "| Record | Column | Original | Cleaned | Rule |", "|---|---|---|---|---|"]
        for e in fixes:
            rule = f"{e.rule} ({e.detail})" if e.detail else e.rule
            lines.append(f"| {e.record} | {e.column} | {fmt_md(e.original)} | {fmt_md(e.cleaned)} | {rule} |")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# MySQL load
# ---------------------------------------------------------------------------

def split_sql(text: str) -> list[str]:
    no_comments = "\n".join(line for line in text.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def load_to_mysql(clean: dict[str, list[dict]]) -> dict[str, int]:
    from db_config import admin_connection, database_name  # only needed when loading

    db = database_name()
    conn = admin_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db}` "
                        "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
            cur.execute(f"USE `{db}`")
            for statement in split_sql(SCHEMA_PATH.read_text(encoding="utf-8")):
                cur.execute(statement)  # DDL: MySQL commits these immediately

            cols = COLUMNS["companies"]
            cur.executemany(
                f"INSERT INTO companies ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                [[r[c] for c in cols] for r in clean["companies"]])
            cur.execute("SELECT company_code, company_id FROM companies")
            ids = dict(cur.fetchall())

            for table in ("financials", "assets", "deals"):
                cols = [c for c in COLUMNS[table] if c != "company_code"]
                cur.executemany(
                    f"INSERT INTO {table} (company_id, {', '.join(cols)}) "
                    f"VALUES (%s, {', '.join(['%s'] * len(cols))})",
                    [[ids[r["company_code"]], *[r[c] for c in cols]] for r in clean[table]])
        conn.commit()

        counts = {}
        with conn.cursor() as cur:
            for table in TABLES:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                counts[table] = cur.fetchone()[0]
        return counts
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-load", action="store_true", help="clean and log only; don't touch MySQL")
    args = parser.parse_args()

    raw = read_raw()
    clean, log = clean_all(raw)
    write_clean_csvs(clean)
    write_log(log, raw, clean)

    kinds = Counter(e.kind for e in log.entries)
    print(f"Cleaned: {kinds['fix']} value fixes, {kinds['removed']} rows removed, "
          f"{kinds['warning']} warnings -> {LOG_PATH.relative_to(PROJECT_ROOT)}")
    for t in TABLES:
        print(f"  {t:<11} {len(raw[t]):>4} raw -> {len(clean[t]):>4} clean")

    if args.skip_load:
        print("Skipped MySQL load (--skip-load).")
        return
    counts = load_to_mysql(clean)
    print("Loaded into MySQL:", ", ".join(f"{t}={n}" for t, n in counts.items()))


if __name__ == "__main__":
    main()
