"""End-to-end: generate messy data, clean it, and compare with the hidden truth."""
import pytest

from clean_and_load import clean_all
from generate_fake_data import generate


@pytest.fixture(scope="module", params=[42, 7])
def run(request):
    truth, raw = generate(seed=request.param)
    clean, log = clean_all(raw)
    return truth, clean, log


def test_row_counts_match_truth(run):
    truth, clean, _ = run
    for table in truth:
        assert len(clean[table]) == len(truth[table]), table


def test_company_fields_recovered(run):
    truth, clean, _ = run
    by_code = {c["company_code"]: c for c in clean["companies"]}
    for t in truth["companies"]:
        c = by_code[t["company_code"]]
        for col in ("name", "sector", "subsector", "country", "hq_city", "acquisition_date", "status"):
            assert c[col] == t[col], (t["company_code"], col)


def test_no_orphans_and_unique_keys(run):
    _, clean, _ = run
    codes = {c["company_code"] for c in clean["companies"]}
    for table in ("financials", "assets", "deals"):
        assert all(r["company_code"] in codes for r in clean[table])
    keys = [(f["company_code"], f["fiscal_year"]) for f in clean["financials"]]
    assert len(keys) == len(set(keys))


def test_values_within_schema_limits(run):
    _, clean, _ = run
    for c in clean["companies"]:
        assert c["ownership_pct"] is None or 0 < c["ownership_pct"] <= 100
    for d in clean["deals"]:
        assert d["equity_stake_pct"] is None or 0 < d["equity_stake_pct"] <= 100
    for f in clean["financials"]:
        assert f["revenue_musd"] is None or f["revenue_musd"] >= 0


def test_every_problem_is_logged(run):
    _, _, log = run
    rules = {e.rule for e in log.entries}
    for expected in ("Exact duplicate row", "Duplicate merged into another row", "Completely blank row",
                     "Orphan row: company does not exist", "Date converted to YYYY-MM-DD",
                     "Number stored as text converted (USD millions)", "Unparseable value set to NULL",
                     "Out-of-range value set to NULL", "Missing value inferred from related field"):
        assert expected in rules


def test_quarterly_financials(run):
    """Every company-year in 2024-2025 has Q1-Q4, quarters add up to the annual
    figures, and some companies miss their revenue target while others beat it."""
    _, clean, _ = run
    quarters = [q for q in clean["quarterly_financials"] if q["fiscal_year"] in (2024, 2025)]
    annual = {(f["company_code"], f["fiscal_year"]): f
              for f in clean["financials"] if f["fiscal_year"] in (2024, 2025)}
    assert annual, "expected 2024-2025 financials"

    by_year: dict = {}
    for q in quarters:
        by_year.setdefault((q["company_code"], q["fiscal_year"]), []).append(q)
    assert set(by_year) == set(annual)

    for key, rows in by_year.items():
        assert sorted(r["quarter"] for r in rows) == [1, 2, 3, 4], key
        for col in ("revenue_musd", "ebitda_musd"):
            if annual[key][col] is None:
                assert all(r[col] is None for r in rows), (key, col)
            else:
                assert round(sum(r[col] for r in rows), 2) == round(annual[key][col], 2), (key, col)

    with_target = [q for q in quarters if q["revenue_target_musd"] is not None]
    assert all(q["revenue_musd"] >= 0 and q["revenue_target_musd"] > 0 for q in with_target)
    missed = {q["company_code"] for q in with_target if q["revenue_musd"] < q["revenue_target_musd"]}
    beat = {q["company_code"] for q in with_target if q["revenue_musd"] > q["revenue_target_musd"]}
    assert missed and beat and missed != beat
    always_beat = {c for c in beat} - missed
    always_missed = missed - beat
    assert always_beat and always_missed


def test_quarterly_2026_through_q3(run):
    """FY2026 runs Q1-Q3 for active companies; in Q3 three energy companies miss target."""
    _, clean, _ = run
    rows = [q for q in clean["quarterly_financials"] if q["fiscal_year"] == 2026]
    active = {c["company_code"] for c in clean["companies"] if c["status"] == "active"}
    with_2025 = {f["company_code"] for f in clean["financials"] if f["fiscal_year"] == 2025}
    assert {q["company_code"] for q in rows} == active & with_2025
    assert {q["quarter"] for q in rows} == {1, 2, 3}
    assert not any(f["fiscal_year"] == 2026 for f in clean["financials"])  # year not closed

    sector = {c["company_code"]: c["sector"] for c in clean["companies"]}
    q3_energy = [q for q in rows if q["quarter"] == 3 and sector[q["company_code"]] == "energy"
                 and q["revenue_musd"] is not None]
    assert len(q3_energy) > 3
    gaps = [q["revenue_musd"] / q["revenue_target_musd"] - 1 for q in q3_energy]
    misses = [g for g in gaps if g < 0]
    assert len(misses) == 3
    assert all(-0.12 < g < -0.02 for g in misses)            # clear but plausible misses
    assert all(0.005 < g < 0.08 for g in gaps if g >= 0)     # the rest beat, modestly
