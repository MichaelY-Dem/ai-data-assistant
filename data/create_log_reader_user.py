"""Create (or update) the read-only MySQL account that data/export_query_log.py reads with.

Connects as the admin account from .env (MYSQL_ADMIN_USER / MYSQL_ADMIN_PASSWORD) and
creates the account QUERY_LOG_READER_USER (default: query_log_reader) with the password
QUERY_LOG_READER_PASSWORD from .env. It gets SELECT on QUERY_LOG_DATABASE.query_log
(default: assistant_logs.query_log) and nothing else: no writes, and no access to the
portfolio database.

The table must exist first: run data/create_logger_user.py before this script.

Safe to rerun: it resets the password to the one in .env and re-applies the grant.
The password is sent straight to MySQL and never printed.

Usage:
    python data/create_log_reader_user.py
"""
from __future__ import annotations

import pymysql

from db_config import (admin_connection, database_name, log_database, log_reader_connection,
                       safe_identifier, setting)

HOST = "localhost"


def create_log_reader() -> None:
    user = safe_identifier(setting("QUERY_LOG_READER_USER", "query_log_reader"))
    password = setting("QUERY_LOG_READER_PASSWORD")
    db = log_database()

    conn = admin_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM information_schema.TABLES "
                        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'query_log'", (db,))
            if not cur.fetchone()[0]:
                raise SystemExit(f"`{db}`.query_log doesn't exist yet. Run data/create_logger_user.py first.")
            cur.execute("CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s", (user, HOST, password))
            cur.execute("ALTER USER %s@%s IDENTIFIED BY %s", (user, HOST, password))
            cur.execute("REVOKE ALL PRIVILEGES, GRANT OPTION FROM %s@%s", (user, HOST))
            cur.execute(f"GRANT SELECT ON `{db}`.`query_log` TO %s@%s", (user, HOST))
            cur.execute("SHOW GRANTS FOR %s@%s", (user, HOST))
            grants = [row[0] for row in cur.fetchall()]
        conn.commit()
    finally:
        conn.close()

    print(f"Account '{user}'@'{HOST}' is ready. Grants:")
    for g in grants:
        print("  ", g)


def _denied(cur, sql: str) -> bool:
    try:
        cur.execute(sql)
    except pymysql.err.MySQLError as exc:
        # 1142 = table access denied (also what MySQL returns for a write without the privilege)
        return exc.args[0] == 1142
    return False


def verify_select_only() -> None:
    conn = log_reader_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM query_log")
            print(f"Check 1 passed: reader can SELECT query_log ({cur.fetchone()[0]} rows).")
            if not _denied(cur, "DELETE FROM query_log WHERE id = 0"):
                raise SystemExit("Check 2 FAILED: the reader account was able to DELETE.")
            print("Check 2 passed: reader cannot change query_log (DELETE denied).")
            portfolio = database_name()
            if not _denied(cur, f"SELECT COUNT(*) FROM `{portfolio}`.`companies`"):
                raise SystemExit("Check 3 FAILED: the reader account can read the portfolio database.")
            print(f"Check 3 passed: reader cannot read the {portfolio} database.")
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    create_log_reader()
    verify_select_only()
