"""Fill in the expected answer for every question in tests/eval/questions.json from MySQL.

Each question carries the SQL that computes its correct answer. This script runs that
SQL as the read-only Analyst account (ASSISTANT_DB_USER) and writes the result into the
question's "expected" field, so the expected values always match the live database.
run_eval.py calls it before every run; you can also run it on its own to inspect them.

What "expected" holds, by check type:
    number          {"numbers": [x]}               first column of the single row
    numbers         {"numbers": [x, y, ...]}       every column of the single row
    names           {"names": [...]}               first column of every row
    names_numbers   {"names": [...], "numbers": [...]}   columns 1 and 2 of every row
    no_data         {"rows_found": 0}              the SQL must count 0 rows
    restricted      {"must_not_contain": [x, ...]} the real answer, which must NOT appear

Usage (from the project root):
    .venv\\Scripts\\python.exe tests\\eval\\build_expected.py
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "data"))

from db_config import reader_connection  # noqa: E402

QUESTIONS_PATH = Path(__file__).resolve().parent / "questions.json"
CHECKS = {"number", "numbers", "names", "names_numbers", "no_data", "restricted"}


def _plain(value):
    """MySQL value -> JSON-friendly value (Decimal -> float, sector codes stay strings)."""
    if isinstance(value, Decimal):
        return round(float(value), 4)
    return value


def _number(qid: str, value) -> float:
    if value is None:
        raise SystemExit(f"{qid}: the SQL returned NULL where a number was expected.")
    return _plain(value)


def expected_for(q: dict, rows: list[tuple]) -> dict:
    qid, check = q["id"], q["check"]
    if check not in CHECKS:
        raise SystemExit(f"{qid}: unknown check {check!r}; expected one of {sorted(CHECKS)}.")

    if check == "no_data":
        count = rows[0][0] if rows else 0
        if count:
            raise SystemExit(f"{qid}: meant to ask about data that doesn't exist, "
                             f"but the SQL found {count} rows. Change the question.")
        return {"rows_found": 0}

    if not rows:
        raise SystemExit(f"{qid}: the SQL returned no rows, so there is nothing to check against. "
                         "Change the question or its SQL.")

    if check == "number":
        return {"numbers": [_number(qid, rows[0][0])]}
    if check in ("numbers", "restricted"):
        values = [_number(qid, v) for v in rows[0]]
        return {"numbers": values} if check == "numbers" else {"must_not_contain": values}
    if check == "names":
        return {"names": [str(r[0]) for r in rows]}
    # names_numbers
    return {"names": [str(r[0]) for r in rows], "numbers": [_number(qid, r[1]) for r in rows]}


def build(path: Path = QUESTIONS_PATH, quiet: bool = False) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = [q["id"] for q in data["questions"]]
    if len(ids) != len(set(ids)):
        raise SystemExit("questions.json has duplicate question ids.")

    conn = reader_connection()  # read-only account: this script can't change data
    try:
        with conn.cursor() as cur:
            for q in data["questions"]:
                cur.execute(q["sql"])
                q["expected"] = expected_for(q, list(cur.fetchall()))
                if not quiet:
                    print(f"{q['id']}  {q['check']:<14} {json.dumps(q['expected'])}")
    finally:
        conn.rollback()
        conn.close()

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not quiet:
        print(f"\nWrote expected values for {len(ids)} questions to {path.relative_to(PROJECT_ROOT)}.")
    return data


if __name__ == "__main__":
    build()
