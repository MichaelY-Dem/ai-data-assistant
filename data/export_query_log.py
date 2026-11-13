"""Export the query_log table to data/exports/query_log.csv for Power BI.

One row per /chat request (and per document summary / one-pager, which are logged too), with every
query_log column plus:
    estimated_minutes_saved   MINUTES_PER_QUESTION for a successful (status 'ok') request by a
                              person, else 0
Evaluation runs (is_eval = 1, sent by tests/eval/run_eval.py) stay in the file so they can be
filtered in Power BI, but they never count as time saved.

ASSUMPTION: each successful question saves an analyst about 15 minutes of looking up the figures
by hand (finding the right tables, writing the query or filtering spreadsheets, checking the
result). It's a planning estimate, not a measurement; change MINUTES_PER_QUESTION to use your own.
The assumption is also written as a comment on the first line of the CSV.

Reads with the read-only account QUERY_LOG_READER_USER / QUERY_LOG_READER_PASSWORD from .env,
which has SELECT on query_log and nothing else (create it with data/create_log_reader_user.py).
The backend's logger account is INSERT-only on purpose, so it can't read the log back.

The file is UTF-8 with a BOM (so Excel and Power BI detect the encoding). asked_at is UTC.
data/exports/ is git-ignored: the log holds real questions and usernames.

Usage (from the project root):
    .venv\\Scripts\\python.exe data\\export_query_log.py
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from db_config import PROJECT_ROOT, log_reader_connection

MINUTES_PER_QUESTION = 15
EXPORT_PATH = PROJECT_ROOT / "data" / "exports" / "query_log.csv"

COLUMNS = ["id", "asked_at", "question", "answer_length", "tools_used", "tool_count",
           "duration_ms", "status", "username", "user_role", "is_eval"]
HEADER_COMMENT = (
    f"# Assumption: estimated_minutes_saved = {MINUTES_PER_QUESTION} minutes per successful question "
    "(status = ok), 0 otherwise; evaluation runs (is_eval = 1) always count 0. "
    "A planning estimate, not a measurement. asked_at is UTC. "
    "In Power BI, remove this first row before using the next row as headers."
)


def fetch_rows() -> list[dict]:
    conn = log_reader_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, asked_at, question, answer_length, tools_used, duration_ms, status, "
                        "username, user_role, is_eval FROM query_log ORDER BY id")
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]
    finally:
        conn.rollback()
        conn.close()


def to_csv_row(row: dict) -> dict:
    tools = json.loads(row["tools_used"]) if row["tools_used"] else []
    out = {k: row.get(k) for k in COLUMNS}
    out["asked_at"] = row["asked_at"].strftime("%Y-%m-%d %H:%M:%S")
    out["tools_used"] = ", ".join(tools)  # plain text is easier to slice in Power BI than JSON
    out["tool_count"] = len(tools)
    out["is_eval"] = 1 if row["is_eval"] else 0
    counts = row["status"] == "ok" and not out["is_eval"]  # people's successful questions only
    out["estimated_minutes_saved"] = MINUTES_PER_QUESTION if counts else 0
    return out


def export(path: Path = EXPORT_PATH) -> int:
    rows = [to_csv_row(r) for r in fetch_rows()]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        f.write(HEADER_COMMENT + "\n")
        writer = csv.DictWriter(f, fieldnames=COLUMNS + ["estimated_minutes_saved"])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


if __name__ == "__main__":
    count = export()
    print(f"Exported {count} rows to {EXPORT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"Assumption: {MINUTES_PER_QUESTION} minutes saved per successful question (status = ok); "
          "evaluation runs count 0.")
