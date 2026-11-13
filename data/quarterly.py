"""Derive quarterly revenue, revenue targets and EBITDA from the clean annual financials.

The quarterly table is built from the *cleaned* `financials` rows (not the raw CSVs),
so it always agrees with what is in MySQL:
  - the four quarters of revenue_musd add up exactly to the annual revenue_musd
  - the four quarters of ebitda_musd add up exactly to the annual ebitda_musd
  - if an annual figure is NULL, its quarterly figures are NULL too

Each company gets a performance profile that decides how it does against its
quarterly revenue targets: "beat" (always above target), "miss" (always below),
or "mixed" (some quarters above, some below). Targets are budget figures, so
they're generated clean; they don't go through the messy-CSV step.

Only company-years that exist in `financials` are covered: a company exited
before 2024 has no 2024/2025 rows.

FY2026 is in progress: Q1-Q3 2026 (the latest completed quarter is Q3) are added
for active companies, projected from their 2025 figures. There is no annual 2026
row in `financials` yet, so these quarters have no yearly total to add up to.
In Q3 2026 exactly ENERGY_Q3_2026_MISSES energy companies miss their revenue
target and the other energy companies beat it.
"""
from __future__ import annotations

import random

YEARS = (2024, 2025)
CURRENT_YEAR, LAST_QUARTER = 2026, 3  # latest completed quarter
ENERGY_Q3_2026_MISSES = 3
COLUMNS = ["company_code", "fiscal_year", "quarter", "revenue_musd",
           "revenue_target_musd", "ebitda_musd"]

# Share of annual revenue earned in Q1..Q4, before a little per-company noise.
SEASONALITY = {
    "energy":       (0.22, 0.27, 0.29, 0.22),  # solar-heavy summer peak
    "data_centers": (0.24, 0.25, 0.25, 0.26),  # steady, slight ramp through the year
    "fiber":        (0.24, 0.25, 0.25, 0.26),
    "toll_roads":   (0.22, 0.26, 0.29, 0.23),  # summer traffic
    "water":        (0.23, 0.25, 0.28, 0.24),  # summer demand
}
# actual / target - 1, drawn per quarter
PROFILE_VARIANCE = {"beat": (0.01, 0.09), "miss": (-0.14, -0.01), "mixed": (-0.06, 0.06)}
PROFILE_WEIGHTS = {"beat": 0.4, "miss": 0.3, "mixed": 0.3}


def _split(total: float, weights: list[float]) -> list[float]:
    """Split total into len(weights) parts rounded to cents that sum exactly to total."""
    parts = [round(total * w, 2) for w in weights[:-1]]
    parts.append(round(total - sum(parts), 2))
    return parts


def build_quarterly(companies: list[dict], financials: list[dict], seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    sector = {c["company_code"]: c["sector"] for c in companies}
    profiles = list(PROFILE_WEIGHTS)
    profile = {code: rng.choices(profiles, weights=list(PROFILE_WEIGHTS.values()))[0]
               for code in sorted(sector)}

    rows = []
    for f in sorted(financials, key=lambda r: (r["company_code"], r["fiscal_year"])):
        code, year = f["company_code"], f["fiscal_year"]
        if year not in YEARS:
            continue
        base = SEASONALITY[sector[code]]
        noisy = [w * rng.uniform(0.96, 1.04) for w in base]
        weights = [w / sum(noisy) for w in noisy]

        revenue = f["revenue_musd"]
        q_revenue = _split(revenue, weights) if revenue is not None else [None] * 4

        ebitda = f["ebitda_musd"]
        if ebitda is None:
            q_ebitda = [None] * 4
        else:  # roughly follows revenue, with quarter-to-quarter margin wobble
            e_noisy = [w * rng.uniform(0.97, 1.03) for w in weights]
            q_ebitda = _split(ebitda, [w / sum(e_noisy) for w in e_noisy])

        lo, hi = PROFILE_VARIANCE[profile[code]]
        for q in range(4):
            target = None
            if q_revenue[q] is not None:
                variance = rng.uniform(lo, hi)
                if abs(variance) < 0.005:  # keep every quarter a clear beat or miss
                    variance = 0.005 if variance >= 0 else -0.005
                target = round(q_revenue[q] / (1 + variance), 2)
            rows.append({"company_code": code, "fiscal_year": year, "quarter": q + 1,
                         "revenue_musd": q_revenue[q], "revenue_target_musd": target,
                         "ebitda_musd": q_ebitda[q]})
    active = {c["company_code"] for c in companies if c["status"] == "active"}
    rows += _current_year(sector, profile, active, financials, seed)
    return rows


def _current_year(sector: dict, profile: dict, active: set[str], financials: list[dict],
                  seed: int) -> list[dict]:
    """Q1..LAST_QUARTER of CURRENT_YEAR for companies still held, projected from last year."""
    rng = random.Random(seed + CURRENT_YEAR)  # separate stream: 2024-2025 rows stay unchanged
    fin = {(f["company_code"], f["fiscal_year"]): f for f in financials}
    prior = CURRENT_YEAR - 1
    held = sorted(code for code in active if (code, prior) in fin)
    energy = [code for code in held  # only companies we can report revenue for
              if sector[code] == "energy" and fin[(code, prior)]["revenue_musd"] is not None]
    energy_missers = set(rng.sample(energy, min(ENERGY_Q3_2026_MISSES, len(energy))))

    rows = []
    for code in held:
        last, before = fin[(code, prior)], fin.get((code, prior - 1))
        revenue, ebitda = last["revenue_musd"], last["ebitda_musd"]
        growth = rng.uniform(-0.02, 0.10)
        if revenue and before and before["revenue_musd"]:  # follow the company's own trend
            growth = min(max(revenue / before["revenue_musd"] - 1, -0.03), 0.15) + rng.uniform(-0.02, 0.02)
        margin = ebitda / revenue if revenue and ebitda is not None else None

        noisy = [w * rng.uniform(0.96, 1.04) for w in SEASONALITY[sector[code]]]
        weights = [w / sum(noisy) for w in noisy]
        lo, hi = PROFILE_VARIANCE[profile[code]]
        for q in range(LAST_QUARTER):
            q_rev = round(revenue * (1 + growth) * weights[q], 2) if revenue is not None else None
            q_ebitda = round(q_rev * margin * rng.uniform(0.96, 1.04), 2) if margin is not None and q_rev is not None else None
            target = None
            if q_rev is not None:
                if q + 1 == LAST_QUARTER and sector[code] == "energy":
                    variance = rng.uniform(-0.11, -0.025) if code in energy_missers else rng.uniform(0.01, 0.07)
                else:
                    variance = rng.uniform(lo, hi)
                    if abs(variance) < 0.005:
                        variance = 0.005 if variance >= 0 else -0.005
                target = round(q_rev / (1 + variance), 2)
            rows.append({"company_code": code, "fiscal_year": CURRENT_YEAR, "quarter": q + 1,
                         "revenue_musd": q_rev, "revenue_target_musd": target, "ebitda_musd": q_ebitda})
    return rows
