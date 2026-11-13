"""Generate a fake infrastructure portfolio with deliberate data-quality problems.

Writes companies.csv, financials.csv, assets.csv and deals.csv to data/raw/.

How it works:
  1. Build a correct "truth" dataset (about 50 companies across five sectors).
  2. Render every value as text the way a sloppy spreadsheet export would:
     mixed date formats, "$1,234.5" style numbers, "45%" percentages, alias
     spellings, stray whitespace, ALL-CAPS names.
  3. Inject specific problems: duplicate rows, blank cells, impossible values,
     orphan rows that reference a company that doesn't exist.

The same --seed always produces the same files.

Usage:
    python data/generate_fake_data.py            # default seed 42
    python data/generate_fake_data.py --seed 7
"""
from __future__ import annotations

import argparse
import copy
import csv
import random
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

from reference import COUNTRIES, SECTORS

RAW_DIR = Path(__file__).resolve().parent / "raw"
N_COMPANIES = 50
FIRST_FY, LAST_FY = 2021, 2025
AS_OF = date(2026, 6, 30)  # fixed so output doesn't depend on when you run it

RAW_COLUMNS = {
    "companies": ["company_code", "name", "sector", "subsector", "country", "hq_city",
                  "founded_year", "ownership_pct", "acquisition_date", "status"],
    "financials": ["company_code", "fiscal_year", "revenue_musd", "ebitda_musd",
                   "net_debt_musd", "capex_musd"],
    "assets": ["company_code", "asset_name", "asset_type", "country", "city", "capacity",
               "capacity_unit", "commissioning_date", "book_value_musd"],
    "deals": ["company_code", "deal_type", "deal_date", "deal_value_musd",
              "equity_stake_pct", "counterparty", "status"],
}

NAME_PREFIXES = [
    "Northwind", "Meridian", "Cascade", "Summit", "Harbor", "Ironbridge", "Bluegate",
    "Crescent", "Pioneer", "Atlas", "Solstice", "Granite", "Evergreen", "Keystone",
    "Lighthouse", "Redwood", "Silverline", "Tidewater", "Westbrook", "Zenith", "Aurora",
    "Beacon", "Cobalt", "Horizon", "Sterling", "Falcon", "Juniper", "Larkspur", "Monarch",
    "Oakridge", "Pinnacle", "Quarry", "Riverbend", "Sandstone", "Thornbury", "Vantage",
    "Willow", "Yarrow", "Alder", "Bramble", "Copperfield", "Driftwood", "Foxglove",
    "Glenmore", "Hawthorn", "Kingsley", "Linden", "Marlow", "Northgate", "Orchard",
    "Prairie", "Rosewood", "Stonebridge",
]
NAME_SUFFIXES = ["Holdings", "Partners", "Infrastructure", "Group", "Ltd", "Inc.", "LLC", "PLC"]

REVENUE_RANGE = {"energy": (40, 600), "data_centers": (30, 900), "fiber": (20, 400),
                 "toll_roads": (50, 700), "water": (60, 500)}
EBITDA_MARGIN = {"energy": (0.55, 0.80), "data_centers": (0.35, 0.55), "fiber": (0.20, 0.60),
                 "toll_roads": (0.60, 0.80), "water": (0.40, 0.60)}

# Spellings the "source systems" use instead of our canonical values
SECTOR_VARIANTS = {
    "energy": ["Energy", "ENERGY", "energy ", "Renewables", "Power & Energy"],
    "data_centers": ["Data Centers", "Data Centres", "data centre", "DataCenters"],
    "fiber": ["Fiber", "Fibre", "FIBER", "Fibre Optic"],
    "toll_roads": ["Toll Roads", "Toll Road", "toll-road"],
    "water": ["Water", "WATER", "Water Utilities"],
}
COUNTRY_VARIANTS = {
    "United States": ["USA", "US", "U.S."],
    "United Kingdom": ["UK", "U.K.", "Great Britain"],
    "Germany": ["Deutschland"],
    "Netherlands": ["Holland", "The Netherlands"],
    "Spain": ["España"],
    "Brazil": ["Brasil"],
}
COMPANY_STATUS_VARIANTS = {"active": ["Active", "active", "ACTIVE", "Held"],
                           "exited": ["Exited", "exited", "Sold"]}
DEAL_TYPE_VARIANTS = {
    "acquisition": ["Acquisition", "ACQ", "acquisition "],
    "follow_on": ["Follow-on", "follow on", "Add-on"],
    "refinancing": ["Refinancing", "Refi"],
    "partial_exit": ["Partial Exit", "partial sale"],
    "full_exit": ["Full Exit", "Exit", "Sale"],
}
DEAL_STATUS_VARIANTS = {"closed": ["Closed", "CLOSED", "closed ", "Completed"],
                        "pending": ["Pending", "Signed"],
                        "terminated": ["Terminated", "Cancelled"]}


def rand_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, max(0, (end - start).days)))


# ---------------------------------------------------------------------------
# 1. Truth data
# ---------------------------------------------------------------------------

def build_truth(rng: random.Random, fake: Faker) -> dict[str, list[dict]]:
    companies, financials, assets, deals = [], [], [], []
    sectors = [s for s in SECTORS for _ in range(N_COMPANIES // len(SECTORS))]
    rng.shuffle(sectors)
    prefixes = rng.sample(NAME_PREFIXES, N_COMPANIES)

    def counterparty(kind: str) -> str:
        words = {"seller": ["Capital", "Infrastructure Fund", "Pension Fund", "Holdings"],
                 "bank": ["Bank", "Savings Bank", "Credit Union"]}[kind]
        return f"{fake.last_name()} {rng.choice(words)}"

    for i, (prefix, sector) in enumerate(zip(prefixes, sectors), start=1):
        info = SECTORS[sector]
        code = f"CO-{i:03d}"
        subsector = rng.choice(list(info["subsectors"]))
        country = rng.choice(list(COUNTRIES))
        hq_city = rng.choice(COUNTRIES[country])
        founded = rng.randint(1965, 2016)
        acquired = rand_date(rng, max(date(founded + 2, 1, 1), date(2012, 1, 1)), date(2024, 12, 31))
        exited = acquired.year <= 2020 and rng.random() < 0.3
        exit_date = rand_date(rng, date(2022, 1, 1), date(2025, 12, 31)) if exited else None
        ownership = 100.0 if rng.random() < 0.2 else round(rng.uniform(15, 99), 1)

        companies.append({
            "company_code": code,
            "name": f"{prefix} {rng.choice(info['name_words'])} {rng.choice(NAME_SUFFIXES)}",
            "sector": sector, "subsector": subsector, "country": country, "hq_city": hq_city,
            "founded_year": founded, "ownership_pct": ownership, "acquisition_date": acquired,
            "status": "exited" if exited else "active",
        })

        # Financials: one row per fiscal year we owned it
        first = max(FIRST_FY, acquired.year)
        last = exit_date.year if exit_date else LAST_FY
        first = min(first, last)
        revenue = rng.uniform(*REVENUE_RANGE[sector])
        growth = rng.uniform(-0.03, 0.15)
        loss_making = sector == "fiber" and rng.random() < 0.4
        for year in range(first, last + 1):
            revenue *= 1 + growth
            margin = rng.uniform(*EBITDA_MARGIN[sector])
            if loss_making and year < first + 2:
                margin = rng.uniform(-0.15, -0.02)  # fibre build-out years lose money
            rev = round(revenue, 1)
            financials.append({
                "company_code": code, "fiscal_year": year, "revenue_musd": rev,
                "ebitda_musd": round(rev * margin, 1),
                "net_debt_musd": round(rev * rng.uniform(3, 8), 1),
                "capex_musd": round(rev * rng.uniform(0.1, 0.5), 1),
            })

        # Assets
        asset_type = info["subsectors"][subsector]
        names_used: set[str] = set()
        for _ in range(rng.randint(1, 4)):
            city = hq_city if rng.random() < 0.6 else rng.choice(COUNTRIES[country])
            name = f"{city} {asset_type}"
            n = 2
            while name in names_used:
                name = f"{city} {asset_type} {'I' * n}"
                n += 1
            names_used.add(name)
            lo, hi = info["capacity_range"]
            cap = round(rng.uniform(lo, hi), 1) if info["unit"] == "MW" else rng.randint(lo, hi)
            assets.append({
                "company_code": code, "asset_name": name, "asset_type": asset_type,
                "country": country, "city": city, "capacity_value": cap,
                "capacity_unit": info["unit"],
                "commissioning_date": rand_date(rng, date(max(founded, 1995), 1, 1), date(2025, 12, 31)),
                "book_value_musd": round(rng.uniform(30, 1500), 1),
            })

        # Deals
        entry_value = round(rng.uniform(150, 3000), 1)
        deals.append({"company_code": code, "deal_type": "acquisition", "deal_date": acquired,
                      "deal_value_musd": entry_value, "equity_stake_pct": ownership,
                      "counterparty": counterparty("seller"), "status": "closed"})
        window_end = (exit_date - timedelta(days=30)) if exit_date else AS_OF
        window_start = acquired + timedelta(days=60)
        if window_start < window_end:
            for _ in range(rng.randint(0, 2)):
                deals.append({"company_code": code, "deal_type": "follow_on",
                              "deal_date": rand_date(rng, window_start, window_end),
                              "deal_value_musd": round(rng.uniform(20, 400), 1),
                              "equity_stake_pct": None, "counterparty": counterparty("seller"),
                              "status": "closed"})
            if rng.random() < 0.4:
                deals.append({"company_code": code, "deal_type": "refinancing",
                              "deal_date": rand_date(rng, window_start, window_end),
                              "deal_value_musd": round(rng.uniform(100, 1500), 1),
                              "equity_stake_pct": None, "counterparty": counterparty("bank"),
                              "status": "closed"})
        if exit_date:
            deals.append({"company_code": code, "deal_type": "full_exit", "deal_date": exit_date,
                          "deal_value_musd": round(entry_value * rng.uniform(1.2, 2.5), 1),
                          "equity_stake_pct": ownership, "counterparty": counterparty("seller"),
                          "status": "closed"})

    # A few live and failed deals
    active = [c for c in companies if c["status"] == "active"]
    for c in rng.sample(active, 3):
        deals.append({"company_code": c["company_code"], "deal_type": "partial_exit",
                      "deal_date": rand_date(rng, date(2026, 7, 1), date(2026, 10, 31)),
                      "deal_value_musd": round(rng.uniform(100, 900), 1),
                      "equity_stake_pct": round(rng.uniform(10, 40), 1),
                      "counterparty": counterparty("seller"), "status": "pending"})
    for c in rng.sample(active, 2):
        deals.append({"company_code": c["company_code"], "deal_type": "follow_on",
                      "deal_date": rand_date(rng, date(2023, 1, 1), date(2025, 12, 31)),
                      "deal_value_musd": round(rng.uniform(50, 300), 1), "equity_stake_pct": None,
                      "counterparty": counterparty("seller"), "status": "terminated"})

    return {"companies": companies, "financials": financials, "assets": assets, "deals": deals}


# ---------------------------------------------------------------------------
# 2. Render values as messy text
# ---------------------------------------------------------------------------

class Messer:
    def __init__(self, rng: random.Random):
        self.rng = rng

    def date(self, d: date | None) -> str:
        if d is None:
            return ""
        if self.rng.random() < 0.45:
            return d.isoformat()
        return self.rng.choice([
            d.strftime("%d/%m/%Y"),          # slashes: day first (European export)
            d.strftime("%m-%d-%Y"),          # dashes, month first (US export)
            d.strftime("%B %d, %Y"),         # "March 14, 2019"
            d.strftime("%d-%b-%Y"),          # "14-Mar-2019"
            d.strftime("%Y/%m/%d"),
            str((d - date(1899, 12, 30)).days),  # Excel serial number
        ])

    def money(self, x: float | None) -> str:
        if x is None:
            return ""
        if x < 0:
            return self.rng.choice([f"({abs(x):,.2f})", f"{x:.2f}", f"-${abs(x):,.1f}"])
        if self.rng.random() < 0.5:
            return f"{x:.2f}"
        return self.rng.choice([f"{x:,.2f}", f"${x:,.1f}", f"USD {x:.1f}", f" {x:.1f} ", f"{x:g}m"])

    def deal_value(self, x: float | None) -> str:
        if x is None:
            return ""
        if self.rng.random() < 0.35:
            return f"{x:.1f}"
        options = [f"${x:,.1f}", f"{x:g}m", f"USD {x:g} million", f"{x * 1_000_000:.0f}"]
        if x >= 1000:
            options.append(f"${x / 1000:g}bn")
        return self.rng.choice(options)

    def pct(self, p: float | None) -> str:
        if p is None:
            return ""
        if self.rng.random() < 0.4:
            return str(p)
        return self.rng.choice([f"{p}%", f"{p} %", f"{p / 100:.3f}"])  # last one is a fraction

    def year(self, y: int, fiscal: bool = False) -> str:
        if self.rng.random() < 0.7:
            return str(y)
        if fiscal:
            return self.rng.choice([f"FY{y}", f"FY{y % 100:02d}", f"{y}.0"])
        return self.rng.choice([f"{y}.0", f"est. {y}"])

    def text(self, s: str | None) -> str:
        if s is None:
            return ""
        r = self.rng.random()
        if r < 0.05:
            return f"  {s}"
        if r < 0.10:
            return f"{s} "
        if r < 0.13:
            return s.replace(" ", "  ", 1)
        return s

    def variant(self, value: str, variants: dict[str, list[str]], keep_prob: float = 0.0) -> str:
        if self.rng.random() < keep_prob or value not in variants:
            return value
        return self.rng.choice(variants[value])

    def code(self, code: str) -> str:
        if self.rng.random() < 0.75:
            return code
        n = int(code[3:])
        return self.rng.choice([f"co-{n}", f"CO{n:03d}", f" {code} ", f"co {n:03d}"])

    def capacity(self, value, unit: str) -> tuple[str, str]:
        return self.rng.choice([
            (f"{value:g} {unit}", ""),
            (f"{value:g}{unit}", ""),
            (f"{value:,g}", unit),
            (f"{value:g}", unit),
            (f"{value:g}", unit),
        ])


def render_raw(truth: dict[str, list[dict]], rng: random.Random) -> dict[str, list[dict]]:
    m = Messer(rng)
    raw: dict[str, list[dict]] = {}

    raw["companies"] = [{
        "company_code": c["company_code"],
        "name": m.text(c["name"]),
        "sector": m.variant(c["sector"], SECTOR_VARIANTS),
        "subsector": c["subsector"],
        "country": m.variant(c["country"], COUNTRY_VARIANTS, keep_prob=0.6),
        "hq_city": m.text(c["hq_city"]),
        "founded_year": m.year(c["founded_year"]),
        "ownership_pct": m.pct(c["ownership_pct"]),
        "acquisition_date": m.date(c["acquisition_date"]),
        "status": m.variant(c["status"], COMPANY_STATUS_VARIANTS),
    } for c in truth["companies"]]

    raw["financials"] = [{
        "company_code": m.code(f["company_code"]),
        "fiscal_year": m.year(f["fiscal_year"], fiscal=True),
        "revenue_musd": m.money(f["revenue_musd"]),
        "ebitda_musd": m.money(f["ebitda_musd"]),
        "net_debt_musd": m.money(f["net_debt_musd"]),
        "capex_musd": m.money(f["capex_musd"]),
    } for f in truth["financials"]]

    raw["assets"] = []
    for a in truth["assets"]:
        cap, unit = m.capacity(a["capacity_value"], a["capacity_unit"])
        raw["assets"].append({
            "company_code": m.code(a["company_code"]),
            "asset_name": m.text(a["asset_name"]),
            "asset_type": a["asset_type"],
            "country": m.variant(a["country"], COUNTRY_VARIANTS, keep_prob=0.6),
            "city": m.text(a["city"]),
            "capacity": cap, "capacity_unit": unit,
            "commissioning_date": m.date(a["commissioning_date"]),
            "book_value_musd": m.money(a["book_value_musd"]),
        })

    raw["deals"] = [{
        "company_code": m.code(d["company_code"]),
        "deal_type": m.variant(d["deal_type"], DEAL_TYPE_VARIANTS),
        "deal_date": m.date(d["deal_date"]),
        "deal_value_musd": m.deal_value(d["deal_value_musd"]),
        "equity_stake_pct": m.pct(d["equity_stake_pct"]),
        "counterparty": m.text(d["counterparty"]),
        "status": m.variant(d["status"], DEAL_STATUS_VARIANTS),
    } for d in truth["deals"]]
    return raw


# ---------------------------------------------------------------------------
# 3. Inject specific problems
# ---------------------------------------------------------------------------

def inject_problems(raw: dict[str, list[dict]], rng: random.Random) -> None:
    def pick(rows, k):
        return rng.sample(range(len(rows)), k)

    def insert_randomly(rows, row):
        rows.insert(rng.randint(0, len(rows)), row)

    co = raw["companies"]
    for i in pick(co, 2):
        co[i]["sector"] = ""            # recoverable from subsector
    for i in pick(co, 2):
        co[i]["country"] = ""           # recoverable from hq_city
    blank_founded = pick(co, 2)
    for i in blank_founded:
        co[i]["founded_year"] = ""
    co[pick(co, 1)[0]]["founded_year"] = "2109"     # typo, impossible
    co[pick(co, 1)[0]]["ownership_pct"] = ""
    co[pick(co, 1)[0]]["ownership_pct"] = "150"     # impossible
    for i in pick(co, 2):
        co[i]["acquisition_date"] = ""  # recoverable from the acquisition deal
    for i in pick(co, 3):
        co[i]["name"] = co[i]["name"].strip().upper()

    # Near-duplicate: same company, different formatting, but this copy HAS the
    # founded year that the original is missing
    near = copy.deepcopy(co[blank_founded[0]])
    near["name"] = near["name"].strip().lower() + " "
    near["founded_year"] = "1999"
    near["ownership_pct"] = ""
    # Second near-duplicate: blank name, otherwise the same
    near2 = copy.deepcopy(co[pick(co, 1)[0]])
    near2["name"] = ""
    exact = [copy.deepcopy(co[i]) for i in pick(co, 3)]
    for row in [near, near2, *exact]:
        insert_randomly(co, row)
    insert_randomly(co, {k: "" for k in RAW_COLUMNS["companies"]})  # fully blank row

    fin = raw["financials"]
    for i, col in zip(pick(fin, 4), ["net_debt_musd", "capex_musd", "net_debt_musd", "ebitda_musd"]):
        fin[i][col] = rng.choice(["N/A", "", "-"])
    neg = fin[pick(fin, 1)[0]]
    neg["revenue_musd"] = "-" + neg["revenue_musd"].strip().lstrip("$").replace("USD ", "")
    exact = [copy.deepcopy(fin[i]) for i in pick(fin, 6)]
    for row in exact:
        insert_randomly(fin, row)
    for i in pick(fin, 2):  # conflicting duplicates appear later in the file
        conflict = copy.deepcopy(fin[i])
        conflict["revenue_musd"] = "999.99"
        fin.append(conflict)
    for year in ("2023", "2024"):
        fin.append({"company_code": "CO-099", "fiscal_year": year, "revenue_musd": "120.00",
                    "ebitda_musd": "60.00", "net_debt_musd": "400.00", "capex_musd": "20.00"})

    assets = raw["assets"]
    assets[pick(assets, 1)[0]]["commissioning_date"] = "31/02/2020"  # 31 February
    for i in pick(assets, 2):
        assets[i]["capacity"], assets[i]["capacity_unit"] = "", ""
    for i in pick(assets, 2):
        assets[i]["commissioning_date"] = "2027-09-01"  # under construction
    for row in [copy.deepcopy(assets[i]) for i in pick(assets, 3)]:
        insert_randomly(assets, row)

    deals = raw["deals"]
    for i in pick(deals, 2):
        deals[i]["deal_value_musd"] = ""
    deals[pick(deals, 1)[0]]["deal_value_musd"] = "undisclosed"
    stake_rows = [i for i, d in enumerate(deals) if d["equity_stake_pct"]]
    deals[rng.choice(stake_rows)]["equity_stake_pct"] = "150%"
    for row in [copy.deepcopy(deals[i]) for i in pick(deals, 2)]:
        insert_randomly(deals, row)
    deals.append({"company_code": "CO-099", "deal_type": "Acquisition", "deal_date": "2022-05-01",
                  "deal_value_musd": "$450.0", "equity_stake_pct": "60%",
                  "counterparty": "Unknown Seller", "status": "Closed"})


def generate(seed: int = 42) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Return (truth, raw). Truth holds Python values; raw holds messy strings."""
    rng = random.Random(seed)
    fake = Faker()
    fake.seed_instance(seed)
    truth = build_truth(rng, fake)
    raw = render_raw(truth, rng)
    inject_problems(raw, rng)
    return truth, raw


def write_csvs(raw: dict[str, list[dict]], out_dir: Path = RAW_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for table, rows in raw.items():
        # utf-8-sig adds a BOM so Excel on Windows shows "São Paulo" correctly
        with open(out_dir / f"{table}.csv", "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=RAW_COLUMNS[table])
            writer.writeheader()
            writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    truth, raw = generate(args.seed)
    write_csvs(raw)
    print(f"Wrote messy CSVs to {RAW_DIR}")
    for table in RAW_COLUMNS:
        print(f"  {table:<11} {len(raw[table]):>4} raw rows  ({len(truth[table])} real records)")


if __name__ == "__main__":
    main()
