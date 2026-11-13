"""Read MySQL settings from .env and open connections. Never prints passwords."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pymysql
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def setting(name: str, default: str | None = None) -> str:
    value = os.getenv(name) or default
    if not value:
        raise SystemExit(
            f"Missing {name}. Copy .env.example to .env and fill in {name} (see README)."
        )
    return value


def safe_identifier(name: str) -> str:
    """Database/user names get put into SQL text, so only allow letters, digits, _."""
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", name):
        raise SystemExit(f"Refusing unsafe MySQL identifier: {name!r}")
    return name


def database_name() -> str:
    return safe_identifier(setting("MYSQL_DATABASE", "portfolio"))


def connect(user_var: str, password_var: str, database: str | None = None,
            default_user: str | None = None) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=setting("MYSQL_HOST", "localhost"),
        port=int(setting("MYSQL_PORT", "3306")),
        user=setting(user_var, default_user),
        password=setting(password_var),
        database=database,
        charset="utf8mb4",
        autocommit=False,
    )


def admin_connection(database: str | None = None) -> pymysql.connections.Connection:
    return connect("MYSQL_ADMIN_USER", "MYSQL_ADMIN_PASSWORD", database)


def reader_connection() -> pymysql.connections.Connection:
    """Read-only account with SELECT on the whole portfolio database (Analyst role)."""
    return connect("ASSISTANT_DB_USER", "ASSISTANT_DB_PASSWORD", database_name())


def viewer_connection() -> pymysql.connections.Connection:
    """Read-only account with SELECT on every portfolio table except deals (Viewer role)."""
    return connect("ASSISTANT_VIEWER_DB_USER", "ASSISTANT_VIEWER_DB_PASSWORD", database_name(),
                   default_user="assistant_viewer")
