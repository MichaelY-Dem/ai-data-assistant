"""Tests for the accuracy-evaluation grader, its expected-value builder and the query_log export.

Offline: no backend, Claude or MySQL. The live evaluation is tests/eval/run_eval.py.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parent / "eval"
sys.path.insert(0, str(EVAL_DIR))

import backend.query_log as query_log  # noqa: E402
import export_query_log  # noqa: E402  (data/ is on sys.path via conftest)
from build_expected import expected_for  # noqa: E402
from run_eval import grade, numbers_in  # noqa: E402


def q(check, expected, **extra):
    return {"id": "T", "check": check, "expected": expected, **extra}


# --- questions.json ----------------------------------------------------------

def test_question_set_shape():
    questions = json.loads((EVAL_DIR / "questions.json").read_text(encoding="utf-8"))["questions"]
    assert len(questions) == 25
    assert len({x["id"] for x in questions}) == 25
    checks = [x["check"] for x in questions]
    assert checks.count("no_data") == 1 and checks.count("restricted") == 1
    restricted = next(x for x in questions if x["check"] == "restricted")
    assert restricted["as_role"] == "viewer" and "deals" in restricted["sql"]
    assert all(x["sql"].lstrip().upper().startswith("SELECT") for x in questions)


# --- build_expected ----------------------------------------------------------

def test_expected_values_by_check_type():
    assert expected_for(q("number", None), [(50,)]) == {"numbers": [50]}
    assert expected_for(q("numbers", None), [(2, 531.0)]) == {"numbers": [2, 531.0]}
    assert expected_for(q("names", None), [("A",), ("B",)]) == {"names": ["A", "B"]}
    assert expected_for(q("names_numbers", None), [("A", 1), ("B", 2)]) == {"names": ["A", "B"], "numbers": [1, 2]}
    assert expected_for(q("no_data", None), [(0,)]) == {"rows_found": 0}
    assert expected_for(q("restricted", None), [(1002.3,)]) == {"must_not_contain": [1002.3]}


@pytest.mark.parametrize("check,rows", [
    ("no_data", [(3,)]),        # the "missing" data exists after all
    ("names", []),              # nothing to check against
    ("number", [(None,)]),      # NULL where a number is expected
])
def test_expected_values_refuse_bad_questions(check, rows):
    with pytest.raises(SystemExit):
        expected_for(q(check, None), rows)


# --- grading -----------------------------------------------------------------

def test_numbers_in_skips_codes_and_reads_billions():
    assert numbers_in("Q3 FY2026: USD 1,237.4m, 12.5%, USD 0.4bn") == [
        (1237.4, False), (12.5, False), (400.0, True)]


@pytest.mark.parametrize("answer,ok", [
    ("Energy companies averaged **USD 454.6m**.", True),     # rounded to one decimal
    ("Energy companies averaged USD 455.0m.", False),        # wrong
])
def test_number_tolerance(answer, ok):
    assert grade(q("number", {"numbers": [454.5875]}), answer)[0] is ok


def test_billions_get_their_own_rounding_allowance():
    assert grade(q("number", {"numbers": [131035.1]}), "Closed deals total USD 131.0bn.")[0]
    assert not grade(q("number", {"numbers": [131035.1]}), "Closed deals total USD 132.0bn.")[0]


def test_names_are_matched_case_and_underscore_insensitively():
    assert grade(q("names_numbers", {"names": ["data_centers"], "numbers": [5809.7]}),
                 "**Data Centers** led with USD 5,809.7m.")[0]


def test_ranking_order_is_checked():
    exp = {"names": ["Willow", "Sandstone"]}
    assert grade(q("names", exp, ordered=True), "1. Willow\n2. Sandstone")[0]
    passed, why = grade(q("names", exp, ordered=True), "1. Sandstone\n2. Willow")
    assert not passed and "order" in why


def test_no_data_answer():
    exp = {"rows_found": 0}
    assert grade(q("no_data", exp), "The database has no data for FY2019; it covers 2021-2025.")[0]
    assert not grade(q("no_data", exp), "Total revenue in FY2019 was USD 9,000m.")[0]


def test_restricted_answer_must_refuse_without_leaking():
    exp = {"must_not_contain": [1002.3]}
    assert grade(q("restricted", exp), "Deal data is restricted for the Viewer role.")[0]
    passed, why = grade(q("restricted", exp), "Restricted, but pending deals total USD 1,002.3m.")
    assert not passed and "LEAKED" in why


# --- query_log -----------------------------------------------------------------

def test_query_log_insert_includes_is_eval(monkeypatch):
    executed = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params):
            executed.append((sql, params))

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(query_log, "connect", lambda *a, **k: FakeConn())
    query_log.write_query_log(asked_at=dt.datetime(2026, 11, 13, tzinfo=dt.timezone.utc), question="Q",
                              answer_length=1, tools_used=[], duration_ms=5, status="ok",
                              username="ana", user_role="analyst", is_eval=True)
    sql, params = executed[0]
    assert "is_eval" in sql and sql.count("%s") == len(params)
    assert params[-1] == 1


# --- export --------------------------------------------------------------------

def _log_row(**overrides):
    row = {"id": 1, "asked_at": dt.datetime(2026, 11, 13, 9, 30), "question": "How many?",
           "answer_length": 10, "tools_used": '["list_tables", "run_query"]', "duration_ms": 1200,
           "status": "ok", "username": "ana", "user_role": "analyst", "is_eval": 0}
    return {**row, **overrides}


def test_export_counts_minutes_only_for_successful_human_questions():
    minutes = export_query_log.MINUTES_PER_QUESTION
    assert export_query_log.to_csv_row(_log_row())["estimated_minutes_saved"] == minutes
    assert export_query_log.to_csv_row(_log_row(status="error"))["estimated_minutes_saved"] == 0
    eval_row = export_query_log.to_csv_row(_log_row(is_eval=1))
    assert eval_row["is_eval"] == 1 and eval_row["estimated_minutes_saved"] == 0


def test_export_row_format():
    out = export_query_log.to_csv_row(_log_row())
    assert out["asked_at"] == "2026-11-13 09:30:00"
    assert out["tools_used"] == "list_tables, run_query" and out["tool_count"] == 2
    assert out["is_eval"] == 0


def test_export_file_has_assumption_comment_then_header(monkeypatch, tmp_path):
    monkeypatch.setattr(export_query_log, "fetch_rows", lambda: [_log_row(), _log_row(id=2, is_eval=1)])
    path = tmp_path / "query_log.csv"
    assert export_query_log.export(path) == 2
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("# Assumption:") and "15 minutes" in lines[0] and "is_eval" in lines[0]
    assert lines[1].split(",")[-2:] == ["is_eval", "estimated_minutes_saved"]
    assert lines[2].endswith(",0,15") and lines[3].endswith(",1,0")


# --- Number words (one..twenty) for small whole-number answers ---------------------------------

def test_count_written_as_word_passes():
    answer = "Two deals were terminated, and their combined value was USD 531.0m."
    assert grade(q("numbers", {"numbers": [2, 531.0]}), answer)[0]


def test_unrelated_number_words_do_not_create_a_false_pass():
    passed, why = grade(q("number", {"numbers": [3]}), "One of the two deals was terminated.")
    assert not passed
    assert why == "missing numbers: 3"


def test_digits_still_match():
    assert grade(q("numbers", {"numbers": [2, 531.0]}), "2 deals were terminated, worth USD 531.0m.")[0]


@pytest.mark.parametrize("expected", [2.5, 21, 0])
def test_number_words_ignored_outside_whole_numbers_1_to_20(expected):
    answer = "Two, twenty-one, twenty, two and a half, zero."
    assert not grade(q("number", {"numbers": [expected]}), answer)[0]
