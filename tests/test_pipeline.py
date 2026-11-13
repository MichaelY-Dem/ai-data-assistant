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
