"""Write one row per /chat request to query_log, as the INSERT-only logger account.

The table and account are created by data/create_logger_user.py. Settings come
from .env: QUERY_LOGGER_USER, QUERY_LOGGER_PASSWORD and QUERY_LOG_DATABASE.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from db_config import connect, safe_identifier, setting

log = logging.getLogger("backend.query_log")

INSERT_SQL = (
    "INSERT INTO query_log (asked_at, question, answer_length, tools_used, duration_ms, status, "
    "username, user_role) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)


def write_query_log(
    *,
    asked_at: dt.datetime,
    question: str,
    answer_length: int,
    tools_used: list[str],
    duration_ms: int,
    status: str,
    username: str | None = None,
    user_role: str | None = None,
) -> None:
    """Insert one log row. Blocking: call it from a worker thread."""
    database = safe_identifier(setting("QUERY_LOG_DATABASE", "assistant_logs"))
    conn = connect("QUERY_LOGGER_USER", "QUERY_LOGGER_PASSWORD", database)
    try:
        with conn.cursor() as cur:
            cur.execute(INSERT_SQL, (
                asked_at.replace(tzinfo=None),  # stored as naive UTC
                question,
                answer_length,
                json.dumps(tools_used),
                duration_ms,
                status,
                username,
                user_role,
            ))
        conn.commit()
    finally:
        conn.close()


def safe_write_query_log(**row) -> None:
    """Like write_query_log, but a logging failure never breaks the request."""
    try:
        write_query_log(**row)
    except (Exception, SystemExit) as exc:  # SystemExit = a missing .env setting
        log.warning("Could not write to query_log: %s", exc)
