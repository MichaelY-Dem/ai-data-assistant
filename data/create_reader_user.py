"""Create (or update) the read-only MySQL account the app will use.

Reads the account name and password from .env (ASSISTANT_DB_USER and
ASSISTANT_DB_PASSWORD) and connects as the admin account from .env. The password
is sent straight to MySQL and never printed.

What it grants: SELECT on the `portfolio` database only. No INSERT/UPDATE/DELETE,
no DDL, nothing on any other database. The account can only connect from this
machine ('localhost').

Safe to rerun: it resets the password to the one in .env and re-applies the grants.

Usage:
    python data/create_reader_user.py
"""
from __future__ import annotations

import pymysql

from db_config import admin_connection, database_name, reader_connection, safe_identifier, setting

HOST = "localhost"


def create_reader() -> None:
    user = safe_identifier(setting("ASSISTANT_DB_USER", "assistant_reader"))
    password = setting("ASSISTANT_DB_PASSWORD")
    db = database_name()

    conn = admin_connection()
    try:
        with conn.cursor() as cur:
            # %s placeholders are escaped by PyMySQL, so the password can contain any character
            cur.execute("CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s", (user, HOST, password))
            cur.execute("ALTER USER %s@%s IDENTIFIED BY %s", (user, HOST, password))
            cur.execute("REVOKE ALL PRIVILEGES, GRANT OPTION FROM %s@%s", (user, HOST))
            cur.execute(f"GRANT SELECT ON `{db}`.* TO %s@%s", (user, HOST))
            cur.execute("SHOW GRANTS FOR %s@%s", (user, HOST))
            grants = [row[0] for row in cur.fetchall()]
        conn.commit()
    finally:
        conn.close()

    print(f"Account '{user}'@'{HOST}' is ready. Grants:")
    for g in grants:
        print("  ", g)


def verify_read_only() -> None:
    conn = reader_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM companies")
            print(f"Check 1 passed: reader can SELECT ({cur.fetchone()[0]} companies).")
            try:
                cur.execute("DELETE FROM companies WHERE 1 = 0")
            except pymysql.err.MySQLError as exc:
                if exc.args[0] == 1142:  # ER_TABLEACCESS_DENIED_ERROR
                    print("Check 2 passed: reader is blocked from writing (DELETE denied).")
                    return
                raise
            raise SystemExit("Check 2 FAILED: the reader account was able to run DELETE.")
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    create_reader()
    verify_read_only()
