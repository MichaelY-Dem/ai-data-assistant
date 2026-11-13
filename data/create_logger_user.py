"""Create the query_log table and the INSERT-only MySQL account the backend logs with.

Connects as the admin account from .env (MYSQL_ADMIN_USER / MYSQL_ADMIN_PASSWORD) and:
  1. creates the database QUERY_LOG_DATABASE (default: assistant_logs) if needed,
  2. creates the query_log table in it if needed,
  3. creates (or updates) the account QUERY_LOGGER_USER (default: query_logger)
     with the password QUERY_LOGGER_PASSWORD from .env,
  4. grants it INSERT on that one table and nothing else.

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

from db_config import admin_connection, connect, safe_identifier, setting

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
    PRIMARY KEY (id),
    KEY idx_query_log_asked_at (asked_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='One row per POST /chat request'
"""


def log_database() -> str:
    return safe_identifier(setting("QUERY_LOG_DATABASE", "assistant_logs"))


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
                "INSERT INTO query_log (asked_at, question, answer_length, tools_used, duration_ms, status) "
                "VALUES (UTC_TIMESTAMP(3), 'setup check', 0, JSON_ARRAY(), 0, 'ok')"
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
