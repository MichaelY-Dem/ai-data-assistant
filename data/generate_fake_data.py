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

# Ranges the main random stream draws from. They are kept as they were when the dataset
# was first generated, so the same seed still picks the same companies, profiles and
# injected problems; each draw is then rescaled into the realistic ranges below.
DRAW_REVENUE_RANGE = {"energy": (40, 600), "data_centers": (30, 900), "fiber": (20, 400),
                      "toll_roads": (50, 700), "water": (60, 500)}
DRAW_CAPACITY_RANGE = {"energy": (20, 800), "data_centers": (2, 300), "fiber": (50, 5000),
                       "toll_roads": (5, 300), "water": (20, 1000)}
DRAW_FIRST_COMMISSIONING_YEAR = 1995
LAST_COMMISSIONING = date(2025, 12, 31)

# Per-subsector economics, roughly in line with listed infrastructure peers:
#   capacity     size of one asset (unit from reference.SECTORS)
#   first_year   earliest plausible commissioning year for the technology at that scale
#   revenue      USD m of annual revenue per unit of capacity
#   build_cost   USD m to build one unit of capacity (drives book value)
#   life         useful life in years (book value is depreciated over it)
#   margin       EBITDA margin band for a mature company; nothing goes above ~60%
SUBSECTOR_ECONOMICS = {
    # energy (MW)
    "Solar":           dict(capacity=(20, 350), first_year=2009, revenue=0.11, build_cost=1.0, life=30, margin=(0.48, 0.58)),
    "Onshore wind":    dict(capacity=(30, 450), first_year=2000, revenue=0.16, build_cost=1.4, life=25, margin=(0.48, 0.58)),
    "Offshore wind":   dict(capacity=(150, 1000), first_year=2010, revenue=0.38, build_cost=3.8, life=25, margin=(0.46, 0.58)),
    "Battery storage": dict(capacity=(20, 300), first_year=2016, revenue=0.13, build_cost=0.9, life=15, margin=(0.30, 0.45)),
    # data centers (MW of IT load)
    "Hyperscale":      dict(capacity=(30, 220), first_year=2010, revenue=2.0, build_cost=10.0, life=25, margin=(0.40, 0.52)),
    "Colocation":      dict(capacity=(4, 50), first_year=2000, revenue=3.0, build_cost=12.0, life=25, margin=(0.35, 0.47)),
    "Edge":            dict(capacity=(0.5, 6), first_year=2018, revenue=3.5, build_cost=14.0, life=15, margin=(0.22, 0.35)),
    # fiber (route km)
    "FTTH":            dict(capacity=(1000, 9000), first_year=2008, revenue=0.015, build_cost=0.08, life=30, margin=(0.30, 0.50)),
    "Long-haul":       dict(capacity=(400, 5000), first_year=1996, revenue=0.012, build_cost=0.04, life=30, margin=(0.30, 0.48)),
    "Metro":           dict(capacity=(40, 600), first_year=1997, revenue=0.15, build_cost=0.35, life=30, margin=(0.32, 0.50)),
    # toll roads (km)
    "Toll road":       dict(capacity=(20, 220), first_year=1975, revenue=2.5, build_cost=22.0, life=50, margin=(0.50, 0.58)),
    "Bridge & tunnel": dict(capacity=(1, 12), first_year=1970, revenue=40.0, build_cost=250.0, life=60, margin=(0.50, 0.58)),
    # water (ML/day)
    "Water treatment": dict(capacity=(50, 700), first_year=1975, revenue=0.6, build_cost=1.3, life=50, margin=(0.38, 0.50)),
    "Desalination":    dict(capacity=(50, 500), first_year=2003, revenue=1.1, build_cost=2.6, life=30, margin=(0.35, 0.45)),
    "Wastewater":      dict(capacity=(50, 800), first_year=1975, revenue=0.5, build_cost=1.5, life=50, margin=(0.35, 0.48)),
}
MAX_MARGIN = 0.585
CAPEX_TO_REVENUE = {"energy": (0.10, 0.35), "data_centers": (0.35, 0.90), "fiber": (0.30, 0.80),
                    "toll_roads": (0.05, 0.20), "water": (0.25, 0.45)}
NET_DEBT_TO_EBITDA = {"energy": (5.0, 7.5), "data_centers": (4.0, 6.5), "fiber": (4.5, 7.0),
                      "toll_roads": (6.0, 8.5), "water": (5.5, 8.0)}
EV_TO_EBITDA = {"energy": (11, 14), "data_centers": (18, 24), "fiber": (14, 19),
                "toll_roads": (15, 20), "water": (13, 17)}

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


def rescale(x: float, lo: float, hi: float, new_lo: float, new_hi: float) -> float:
    """Map x from [lo, hi] onto [new_lo, new_hi] linearly."""
    return new_lo + (x - lo) / (hi - lo) * (new_hi - new_lo) if hi > lo else new_lo


def rescale_date(d: date, lo: date, hi: date, new_lo: date, new_hi: date) -> date:
    span = (hi - lo).days
    frac = (d - lo).days / span if span else 0.0
    return new_lo + timedelta(days=round(frac * max(0, (new_hi - new_lo).days)))


# ---------------------------------------------------------------------------
# 1. Truth data
# ---------------------------------------------------------------------------

def build_truth(rng: random.Random, fake: Faker, seed: int = 42) -> dict[str, list[dict]]:
    """Every call on `rng` happens in the same order as in the original generator, so a seed
    keeps its companies, profiles and injected problems. The realism parameters (margin,
    leverage, valuation multiple) come from a separate per-company stream."""
    companies, financials, assets, deals = [], [], [], []
    valuation: dict[str, dict] = {}  # per company, for the live and failed deals at the end
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
        side = random.Random(f"{seed}:{code}")  # side stream: doesn't disturb rng
        subsector = rng.choice(list(info["subsectors"]))
        econ = SUBSECTOR_ECONOMICS[subsector]
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

        # Financial draws, one row per fiscal year we owned it. The rows are built after
        # the assets, because installed capacity sets the size of the business.
        first = max(FIRST_FY, acquired.year)
        last = exit_date.year if exit_date else LAST_FY
        first = min(first, last)
        size_draw = rng.uniform(*DRAW_REVENUE_RANGE[sector])
        growth = rng.uniform(-0.03, 0.15)
        loss_making = sector == "fiber" and rng.random() < 0.4
        year_draws = []
        for year in range(first, last + 1):
            margin_noise = rng.uniform(-0.02, 0.02)
            loss_margin = rng.uniform(-0.15, -0.02) if loss_making and year < first + 2 else None
            debt_noise = rng.uniform(-0.06, 0.06)
            capex_ratio = rng.uniform(*CAPEX_TO_REVENUE[sector])
            year_draws.append((year, margin_noise, loss_margin, debt_noise, capex_ratio))

        # Assets
        asset_type = info["subsectors"][subsector]
        names_used: set[str] = set()
        total_capacity = 0.0
        for _ in range(rng.randint(1, 4)):
            city = hq_city if rng.random() < 0.6 else rng.choice(COUNTRIES[country])
            name = f"{city} {asset_type}"
            n = 2
            while name in names_used:
                name = f"{city} {asset_type} {'I' * n}"
                n += 1
            names_used.add(name)
            lo, hi = DRAW_CAPACITY_RANGE[sector]
            raw_cap = rng.uniform(lo, hi) if info["unit"] == "MW" else rng.randint(lo, hi)
            cap = rescale(raw_cap, lo, hi, *econ["capacity"])
            cap = round(cap, 1) if info["unit"] == "MW" else int(round(cap))
            # Not before the company existed, nor before the technology was built at this scale
            draw_start = date(max(founded, DRAW_FIRST_COMMISSIONING_YEAR), 1, 1)
            raw_cod = rand_date(rng, draw_start, LAST_COMMISSIONING)
            start = date(min(max(founded, econ["first_year"]), LAST_COMMISSIONING.year), 1, 1)
            cod = rescale_date(raw_cod, draw_start, LAST_COMMISSIONING, start, LAST_COMMISSIONING)
            remaining_life = max(0.3, 1 - (LAST_COMMISSIONING.year - cod.year) / econ["life"])
            book = cap * econ["build_cost"] * remaining_life * rng.uniform(0.85, 1.15)
            total_capacity += cap
            assets.append({
                "company_code": code, "asset_name": name, "asset_type": asset_type,
                "country": country, "city": city, "capacity_value": cap,
                "capacity_unit": info["unit"], "commissioning_date": cod,
                "book_value_musd": round(book, 1),
            })

        # Financials: revenue in the last year owned follows installed capacity, earlier
        # years are discounted back at the company's growth rate.
        base_margin = side.uniform(*econ["margin"])
        leverage = side.uniform(*NET_DEBT_TO_EBITDA[sector])    # net debt / EBITDA
        multiple = side.uniform(*EV_TO_EBITDA[sector])          # enterprise value / EBITDA
        last_revenue = (total_capacity * econ["revenue"]
                        * rescale(size_draw, *DRAW_REVENUE_RANGE[sector], 0.85, 1.15))

        def revenue_in(year: int) -> float:
            return last_revenue / (1 + growth) ** (last - year)

        for year, margin_noise, loss_margin, debt_noise, capex_ratio in year_draws:
            rev = round(revenue_in(year), 1)
            if loss_margin is not None:      # fibre build-out years lose money
                margin, capex_ratio = loss_margin, capex_ratio * 1.6
            elif loss_making and year < first + 4:  # then ramp up to a mature margin
                margin = base_margin * (0.6 if year < first + 3 else 0.85) + margin_noise
            else:
                margin = min(base_margin + margin_noise, MAX_MARGIN)
            financials.append({
                "company_code": code, "fiscal_year": year, "revenue_musd": rev,
                "ebitda_musd": round(rev * margin, 1),
                "net_debt_musd": round(rev * base_margin * leverage * (1 + debt_noise), 1),
                "capex_musd": round(rev * capex_ratio, 1),
            })

        # Deals, valued from EBITDA: equity value = (EV - net debt) x stake
        def equity_value(year: int) -> float:  # 100% of the equity
            return revenue_in(year) * base_margin * (multiple - leverage)

        ebitda_now = last_revenue * base_margin
        valuation[code] = {"equity": equity_value(last), "ownership": ownership}
        entry_value = round(max(equity_value(acquired.year) * ownership / 100
                                * rng.uniform(0.95, 1.05), 5.0), 1)
        deals.append({"company_code": code, "deal_type": "acquisition", "deal_date": acquired,
                      "deal_value_musd": entry_value, "equity_stake_pct": ownership,
                      "counterparty": counterparty("seller"), "status": "closed"})
        window_end = (exit_date - timedelta(days=30)) if exit_date else AS_OF
        window_start = acquired + timedelta(days=60)
        if window_start < window_end:
            for _ in range(rng.randint(0, 2)):  # expansion equity, pro rata to our stake
                deals.append({"company_code": code, "deal_type": "follow_on",
                              "deal_date": rand_date(rng, window_start, window_end),
                              "deal_value_musd": round(max(ebitda_now * rng.uniform(0.2, 1.0)
                                                           * ownership / 100, 2.0), 1),
                              "equity_stake_pct": None, "counterparty": counterparty("seller"),
                              "status": "closed"})
            if rng.random() < 0.4:  # refinances most of the company's debt
                deals.append({"company_code": code, "deal_type": "refinancing",
                              "deal_date": rand_date(rng, window_start, window_end),
                              "deal_value_musd": round(ebitda_now * leverage * rng.uniform(0.4, 1.0), 1),
                              "equity_stake_pct": None, "counterparty": counterparty("bank"),
                              "status": "closed"})
        if exit_date:
            deals.append({"company_code": code, "deal_type": "full_exit", "deal_date": exit_date,
                          "deal_value_musd": round(entry_value * rng.uniform(1.2, 2.5), 1),
                          "equity_stake_pct": ownership, "counterparty": counterparty("seller"),
                          "status": "closed"})

    # A few live and failed deals
    active = [c for c in companies if c["status"] == "active"]
    for c in rng.sample(active, 3):  # sell part of our holding at a small premium
        v = valuation[c["company_code"]]
        deal_date = rand_date(rng, date(2026, 7, 1), date(2026, 10, 31))
        premium = rng.uniform(1.0, 1.2)
        stake = round(v["ownership"] * rng.uniform(0.2, 0.5), 1)  # never more than we own
        deals.append({"company_code": c["company_code"], "deal_type": "partial_exit",
                      "deal_date": deal_date,
                      "deal_value_musd": round(v["equity"] * stake / 100 * premium, 1),
                      "equity_stake_pct": stake,
                      "counterparty": counterparty("seller"), "status": "pending"})
    for c in rng.sample(active, 2):
        v = valuation[c["company_code"]]
        deal_date = rand_date(rng, date(2023, 1, 1), date(2025, 12, 31))
        deals.append({"company_code": c["company_code"], "deal_type": "follow_on",
                      "deal_date": deal_date,
                      "deal_value_musd": round(max(v["equity"] * v["ownership"] / 100
                                                   * rng.uniform(0.05, 0.15), 2.0), 1),
                      "equity_stake_pct": None,
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
    truth = build_truth(rng, fake, seed)
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
