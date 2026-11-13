"""Create (or update) the restricted read-only MySQL account used for the Viewer role.

Reads the account name and password from .env (ASSISTANT_VIEWER_DB_USER, default
assistant_viewer, and ASSISTANT_VIEWER_DB_PASSWORD) and connects as the admin account
from .env. The password is sent straight to MySQL and never printed.

What it grants: SELECT on the tables listed in data/roles.py VIEWER_TABLES, one table at
a time. Nothing on deals, no writes, no DDL, nothing on any other database. Because
the grants are per table, MySQL itself refuses `SELECT ... FROM deals` for this account,
and information_schema doesn't even list the deals table to it.

Safe to rerun: it resets the password to the one in .env and re-applies the grants.

Usage:
    python data/create_viewer_user.py
"""
from __future__ import annotations

import pymysql

from db_config import admin_connection, database_name, safe_identifier, setting, viewer_connection
from roles import RESTRICTED_TABLES, VIEWER, VIEWER_TABLES

HOST = "localhost"


def create_viewer() -> None:
    user = safe_identifier(setting("ASSISTANT_VIEWER_DB_USER", "assistant_viewer"))
    password = setting("ASSISTANT_VIEWER_DB_PASSWORD")
    db = database_name()

    conn = admin_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s", (user, HOST, password))
            cur.execute("ALTER USER %s@%s IDENTIFIED BY %s", (user, HOST, password))
            cur.execute("REVOKE ALL PRIVILEGES, GRANT OPTION FROM %s@%s", (user, HOST))
            for table in VIEWER_TABLES:
                cur.execute(f"GRANT SELECT ON `{db}`.`{safe_identifier(table)}` TO %s@%s", (user, HOST))
            cur.execute("SHOW GRANTS FOR %s@%s", (user, HOST))
            grants = [row[0] for row in cur.fetchall()]
        conn.commit()
    finally:
        conn.close()

    print(f"Account '{user}'@'{HOST}' is ready. Grants:")
    for g in grants:
        print("  ", g)


def verify_restricted() -> None:
    conn = viewer_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM companies")
            print(f"Check 1 passed: viewer can SELECT companies ({cur.fetchone()[0]} rows).")
            for table in sorted(RESTRICTED_TABLES[VIEWER]):
                try:
                    cur.execute(f"SELECT COUNT(*) FROM `{table}`")
                except pymysql.err.MySQLError as exc:
                    if exc.args[0] == 1142:  # ER_TABLEACCESS_DENIED_ERROR
                        print(f"Check 2 passed: viewer is blocked from reading {table}.")
                        continue
                    raise
                raise SystemExit(f"Check 2 FAILED: the viewer account was able to read {table}.")
            try:
                cur.execute("DELETE FROM companies WHERE 1 = 0")
            except pymysql.err.MySQLError as exc:
                if exc.args[0] == 1142:
                    print("Check 3 passed: viewer is blocked from writing (DELETE denied).")
                    return
                raise
            raise SystemExit("Check 3 FAILED: the viewer account was able to run DELETE.")
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    create_viewer()
    verify_restricted()
