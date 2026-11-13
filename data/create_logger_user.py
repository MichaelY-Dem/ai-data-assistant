"""Create the query_log table and the INSERT-only MySQL account the backend logs with.

Connects as the admin account from .env (MYSQL_ADMIN_USER / MYSQL_ADMIN_PASSWORD) and:
  1. creates the database QUERY_LOG_DATABASE (default: assistant_logs) if needed,
  2. creates the query_log table in it if needed,
  3. creates (or updates) the account QUERY_LOGGER_USER (default: query_logger)
     with the password QUERY_LOGGER_PASSWORD from .env,
  4. grants it INSERT on that one table and nothing else.

Phase 7 added the username and user_role columns (who asked), and Phase 8 the is_eval
column (1 = an automated evaluation run, sent with the X-Eval-Run header); rerunning
the script adds them to an existing table.

The log lives in its own database on purpose: the read-only account Claude queries
through (ASSISTANT_DB_USER) has SELECT on the whole portfolio database, so a log
table there would show up in list_tables and Claude could read past questions.

Safe to rerun: it resets the password to the one in .env and re-applies the grant.
The password is sent straight to MySQL and never printed.

Usage:
    python data/create_logger_user.py
"""
from __future__ import annotations

import pymysql

from db_config import admin_connection, connect, log_database, safe_identifier, setting

HOST = "localhost"

QUERY_LOG_DDL = """
CREATE TABLE IF NOT EXISTS query_log (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    asked_at       DATETIME(3)     NOT NULL COMMENT 'When the request arrived (UTC)',
    question       TEXT            NOT NULL,
    answer_length  INT UNSIGNED    NOT NULL COMMENT 'Characters in the answer (0 on error)',
    tools_used     JSON            NOT NULL COMMENT 'Tool names Claude called, in order',
    duration_ms    INT UNSIGNED    NOT NULL,
    status         VARCHAR(16)     NOT NULL COMMENT 'ok or error',
    username       VARCHAR(64)     NULL     COMMENT 'Signed-in user who asked',
    user_role      VARCHAR(16)     NULL     COMMENT 'analyst or viewer',
    is_eval        TINYINT(1)      NOT NULL DEFAULT 0 COMMENT '1 = automated evaluation run (tests/eval)',
    PRIMARY KEY (id),
    KEY idx_query_log_asked_at (asked_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='One row per POST /chat request'
"""

# Columns added after the table was first created: (name, definition, after column).
ADDED_COLUMNS = [
    ("username", "VARCHAR(64) NULL COMMENT 'Signed-in user who asked'", "status"),
    ("user_role", "VARCHAR(16) NULL COMMENT 'analyst or viewer'", "username"),
    ("is_eval", "TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 = automated evaluation run (tests/eval)'",
     "user_role"),
]


def create_logger() -> None:
    user = safe_identifier(setting("QUERY_LOGGER_USER", "query_logger"))
    password = setting("QUERY_LOGGER_PASSWORD")
    db = log_database()

    conn = admin_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db}` "
                        "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
            cur.execute(f"USE `{db}`")
            cur.execute(QUERY_LOG_DDL)
            cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'query_log'", (db,))
            existing = {row[0] for row in cur.fetchall()}
            for name, definition, after in ADDED_COLUMNS:
                if name not in existing:
                    cur.execute(f"ALTER TABLE query_log ADD COLUMN `{name}` {definition} AFTER `{after}`")
                    print(f"Added column query_log.{name}.")
            cur.execute("CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s", (user, HOST, password))
            cur.execute("ALTER USER %s@%s IDENTIFIED BY %s", (user, HOST, password))
            cur.execute("REVOKE ALL PRIVILEGES, GRANT OPTION FROM %s@%s", (user, HOST))
            cur.execute(f"GRANT INSERT ON `{db}`.`query_log` TO %s@%s", (user, HOST))
            cur.execute("SHOW GRANTS FOR %s@%s", (user, HOST))
            grants = [row[0] for row in cur.fetchall()]
        conn.commit()
    finally:
        conn.close()

    print(f"Table `{db}`.query_log is ready.")
    print(f"Account '{user}'@'{HOST}' is ready. Grants:")
    for g in grants:
        print("  ", g)


def verify_insert_only() -> None:
    conn = connect("QUERY_LOGGER_USER", "QUERY_LOGGER_PASSWORD", log_database())
    try:
        with conn.cursor() as cur:
            # Test insert inside a transaction that is rolled back, so no test row is left behind.
            cur.execute(
                "INSERT INTO query_log (asked_at, question, answer_length, tools_used, duration_ms, status, "
                "username, user_role, is_eval) VALUES (UTC_TIMESTAMP(3), 'setup check', 0, JSON_ARRAY(), 0, 'ok', "
                "NULL, NULL, 0)"
            )
            conn.rollback()
            print("Check 1 passed: logger can INSERT into query_log.")
            try:
                cur.execute("SELECT COUNT(*) FROM query_log")
            except pymysql.err.MySQLError as exc:
                if exc.args[0] == 1142:  # ER_TABLEACCESS_DENIED_ERROR
                    print("Check 2 passed: logger cannot read query_log (SELECT denied).")
                    return
                raise
            raise SystemExit("Check 2 FAILED: the logger account was able to SELECT.")
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    create_logger()
    verify_insert_only()
