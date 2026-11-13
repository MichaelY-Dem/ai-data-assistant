"""MCP server that lets Claude explore the portfolio MySQL database, read-only.

It connects as the read-only account from .env (ASSISTANT_DB_USER /
ASSISTANT_DB_PASSWORD, plus MYSQL_HOST, MYSQL_PORT and MYSQL_DATABASE) and
exposes three tools: list_tables, describe_table and run_query.

Safety comes in layers:
  1. The MySQL account itself only has SELECT on the portfolio database
     (see data/create_reader_user.py).
  2. run_query only accepts a single plain SELECT statement: no comments, no
     second statement, no write/DDL keywords (validate_select below).
  3. Each query runs inside a READ ONLY transaction that is always rolled back,
     with a server-side time limit, and at most MAX_ROWS rows are returned.

Run it over stdio (this is what the Claude desktop app does):
    .venv\\Scripts\\python.exe mcp_server\\server.py

Note: stdout carries the MCP protocol, so this file must never print().
"""
from __future__ import annotations

import datetime as dt
import decimal
import re
import sys
from pathlib import Path
from typing import Any, TypedDict

import pymysql
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

# Reuse the project's .env loading and connection code from data/db_config.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "data"))
from db_config import database_name, reader_connection, setting  # noqa: E402

MAX_ROWS = 200
QUERY_TIMEOUT_MS = 15_000

# Words that must not appear anywhere in a query (outside string literals and
# `backticked` identifiers). INTO blocks SELECT ... INTO OUTFILE / @variable;
# UPDATE also blocks SELECT ... FOR UPDATE row locks.
FORBIDDEN_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE", "UPSERT",
    "DROP", "ALTER", "CREATE", "TRUNCATE", "RENAME",
    "GRANT", "REVOKE", "SET", "CALL", "EXECUTE", "PREPARE", "DEALLOCATE",
    "LOAD", "HANDLER", "LOCK", "UNLOCK", "INTO", "OUTFILE", "DUMPFILE",
    "SHUTDOWN", "KILL", "FLUSH", "RESET", "PURGE", "INSTALL", "UNINSTALL",
}

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

mcp = MCPServer(
    name="portfolio-db",
    instructions=(
        "Read-only access to the 'portfolio' MySQL database (MySQL 8 SQL dialect). "
        "Start with list_tables, then describe_table on the tables you need, then "
        "run_query with a single SELECT. Results are capped at 200 rows, so use "
        "WHERE, GROUP BY, ORDER BY and LIMIT to get the answer directly."
    ),
)


# --------------------------------------------------------------------------- #
# SQL validation
# --------------------------------------------------------------------------- #

def _strip_literals(sql: str) -> str:
    """Return sql with string literals and `identifiers` blanked out.

    Rejects comments and semicolons found outside literals, and unterminated
    literals. What comes back is safe to scan for keywords.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            while True:
                if i >= n:
                    raise ToolError("Rejected: the query has an unterminated quote.")
                if sql[i] == "\\" and quote != "`":
                    i += 2  # backslash escape inside a string
                    continue
                if sql[i] == quote:
                    if i + 1 < n and sql[i + 1] == quote:  # doubled quote = escaped quote
                        i += 2
                        continue
                    break
                i += 1
            i += 1
            out.append(" '' ")
            continue
        if ch == "#" or sql.startswith("--", i) or sql.startswith("/*", i):
            raise ToolError("Rejected: SQL comments are not allowed.")
        if ch == ";":
            raise ToolError("Rejected: only a single statement is allowed (no ';').")
        out.append(ch)
        i += 1
    return "".join(out)


def validate_select(sql: str) -> str:
    """Return the cleaned query if it is a single plain SELECT, else raise ToolError."""
    if not isinstance(sql, str) or not sql.strip():
        raise ToolError("Rejected: the query is empty.")
    query = sql.strip()
    if query.endswith(";"):  # allow one harmless trailing semicolon
        query = query[:-1].rstrip()

    code = _strip_literals(query)
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", code)
    if not words or words[0].upper() != "SELECT":
        raise ToolError("Rejected: only SELECT statements are allowed.")
    bad = sorted({w.upper() for w in words} & FORBIDDEN_KEYWORDS)
    if bad:
        raise ToolError(f"Rejected: forbidden keyword(s) in a read-only query: {', '.join(bad)}.")
    return query


# --------------------------------------------------------------------------- #
# Database helpers
# --------------------------------------------------------------------------- #

def _json_value(value: Any) -> Any:
    """Turn MySQL values (Decimal, dates, bytes) into JSON-friendly ones."""
    if isinstance(value, decimal.Decimal):
        return float(value) if value != value.to_integral_value() else int(value)
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def _fetch(sql: str, params: tuple = (), limit: int | None = None) -> tuple[list[str], list[dict], bool]:
    """Run one statement in a read-only, rolled-back transaction.

    Returns (column names, rows, truncated). With a limit, fetches limit + 1 rows
    so we can tell whether more were available.
    """
    conn = reader_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {QUERY_TIMEOUT_MS}")
            cur.execute("START TRANSACTION READ ONLY")
            cur.execute(sql, params or None)
            columns = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(limit + 1) if limit is not None else cur.fetchall()
        truncated = limit is not None and len(rows) > limit
        rows = rows[:limit] if limit is not None else rows
        return columns, [{k: _json_value(v) for k, v in r.items()} for r in rows], truncated
    except pymysql.err.MySQLError as exc:
        code, msg = (exc.args + ("",))[:2] if exc.args else ("?", str(exc))
        raise ToolError(f"MySQL error {code}: {msg}") from None
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()


def _table_names() -> list[str]:
    _, rows, _ = _fetch(
        "SELECT TABLE_NAME AS name FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME",
        (database_name(),),
    )
    return [r["name"] for r in rows]


class TableList(TypedDict):
    tables: list[str]


class QueryResult(TypedDict):
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #

@mcp.tool(
    title="List tables",
    description=(
        "List every table in the portfolio database (portfolio companies, their "
        "financials, deals and related data). Call this first when you don't yet "
        "know which tables exist or what a table is called. Takes no arguments."
    ),
    annotations=READ_ONLY,
)
def list_tables() -> TableList:
    # Wrapped in a dict on purpose: the MCP SDK turns a bare list return value into
    # one text block per item, which clients show run together ("assetscompanies...").
    # A dict becomes a single JSON text block with the names as separate array items.
    return TableList(tables=_table_names())


@mcp.tool(
    title="Describe table",
    description=(
        "Show the columns of one table in the portfolio database: name, MySQL "
        "type, whether it can be NULL, key (PRI = primary key, MUL = indexed / "
        "foreign key, UNI = unique), default and comment. Call this before "
        "writing a query against a table so you use the real column names and "
        "know how tables join. 'table_name' must be a name returned by list_tables."
    ),
    annotations=READ_ONLY,
)
def describe_table(table_name: str) -> list[dict]:
    if table_name not in _table_names():
        raise ToolError(f"Unknown table {table_name!r}. Use list_tables to see the available tables.")
    _, rows, _ = _fetch(
        "SELECT COLUMN_NAME AS `column`, COLUMN_TYPE AS `type`, IS_NULLABLE AS nullable, "
        "COLUMN_KEY AS `key`, COLUMN_DEFAULT AS `default`, EXTRA AS extra, COLUMN_COMMENT AS comment "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
        "ORDER BY ORDINAL_POSITION",
        (database_name(), table_name),
    )
    return rows


@mcp.tool(
    title="Run read-only SQL query",
    description=(
        "Run ONE read-only SQL SELECT statement (MySQL 8 syntax) against the "
        "portfolio database and return the rows. Use it to answer questions about "
        "the data: counts, totals, averages, rankings, filters, joins, trends. "
        "Rules: a single SELECT only; INSERT/UPDATE/DELETE/DROP/ALTER/CREATE and "
        "other writes, multiple statements (';'), SQL comments and SELECT ... INTO "
        "are rejected. At most 200 rows are returned ('truncated' is true when "
        "there were more), so aggregate or add LIMIT/ORDER BY rather than pulling "
        "raw rows. Queries time out after 15 seconds. Check column names with "
        "describe_table first."
    ),
    annotations=READ_ONLY,
)
def run_query(sql: str) -> QueryResult:
    query = validate_select(sql)
    columns, rows, truncated = _fetch(query, limit=MAX_ROWS)
    return QueryResult(columns=columns, rows=rows, row_count=len(rows), truncated=truncated)


def main() -> None:
    # Fail fast with a clear message (on stderr) if .env is incomplete.
    for name in ("ASSISTANT_DB_USER", "ASSISTANT_DB_PASSWORD"):
        setting(name)
    database_name()
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
