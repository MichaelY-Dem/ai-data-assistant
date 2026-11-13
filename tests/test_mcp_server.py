"""The MCP server's run_query must refuse anything that isn't a single SELECT."""
import sys
from pathlib import Path

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp_server"))
import server  # noqa: E402


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    """Any attempt to reach MySQL fails the test: rejected SQL must never get there."""
    def fail():
        raise AssertionError("query reached the database")
    monkeypatch.setattr(server, "reader_connection", fail)
    monkeypatch.setattr(server, "viewer_connection", fail)
    monkeypatch.setattr(server, "ROLE", "analyst")


def test_run_query_blocks_delete():
    with pytest.raises(ToolError, match="only SELECT"):
        server.run_query("DELETE FROM companies")


def test_delete_blocked_through_mcp_tool_call():
    """Same check, but going through the MCP server the way Claude calls it."""
    with pytest.raises(ToolError, match="only SELECT"):
        anyio.run(server.mcp.call_tool, "run_query", {"sql": "DELETE FROM companies"})


@pytest.mark.parametrize("sql", [
    "SELECT 1; DELETE FROM companies",            # second statement
    "SELECT * FROM companies -- DROP TABLE x",    # comment
    "SELECT * FROM companies /* hi */",           # block comment
    "SELECT * FROM companies INTO OUTFILE '/tmp/x'",
    "SELECT * FROM companies FOR UPDATE",
    "UPDATE companies SET name = 'x'",
    "DROP TABLE companies",
    "WITH x AS (SELECT 1) DELETE FROM companies",
    "",
])
def test_non_select_rejected(sql):
    with pytest.raises(ToolError):
        server.validate_select(sql)


@pytest.mark.parametrize("sql", [
    "SELECT name FROM companies WHERE sector = 'Retail; -- delete'",  # tricks inside a string are fine
    "select count(*) from companies;",
    "SELECT `update` FROM t",
])
def test_plain_selects_allowed(sql):
    assert server.validate_select(sql).lower().startswith("select")


def test_list_tables_returns_separate_names(monkeypatch):
    """Through MCP, the names must arrive as one JSON array, not one text block each."""
    import json
    names = ["assets", "companies", "deals", "financials", "quarterly_financials"]
    monkeypatch.setattr(server, "_table_names", lambda: names)
    result = anyio.run(server.mcp.call_tool, "list_tables", {})
    assert len(result.content) == 1
    assert json.loads(result.content[0].text) == {"tables": names}
    assert result.structured_content == {"tables": names}


# --------------------------------------------------------------------------- #
# Viewer role: no deals
# --------------------------------------------------------------------------- #

class FakeCursor:
    def __init__(self, conn):
        self.conn, self.description, self._rows = conn, None, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append(sql)
        if self.conn.error and not sql.startswith(("SET", "START")):
            raise self.conn.error
        self.description = [("n",)]
        self._rows = [{"n": 1}]

    def fetchmany(self, n):
        return self._rows[:n]

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, error=None):
        self.executed, self.error = [], error

    def cursor(self, *_):
        return FakeCursor(self)

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def viewer(monkeypatch):
    """Run the server as the Viewer role, with a fake Viewer MySQL connection."""
    conn = FakeConnection()
    monkeypatch.setattr(server, "ROLE", "viewer")
    monkeypatch.setattr(server, "viewer_connection", lambda: conn)
    return conn


@pytest.mark.parametrize("sql", [
    "SELECT * FROM deals",
    "select deal_value_musd from DEALS",
    "SELECT * FROM `deals`",
    "SELECT * FROM portfolio.deals",
    "SELECT * FROM `portfolio`.`deals`",
    "SELECT c.name FROM companies c JOIN deals d ON d.company_id = c.company_id",
    "SELECT name FROM companies WHERE company_id IN (SELECT company_id FROM deals)",
    "SELECT COUNT(*) FROM (SELECT * FROM deals) x",
])
def test_viewer_cannot_query_deals(viewer, sql):
    with pytest.raises(ToolError, match="restricted for the Viewer role"):
        server.run_query(sql)
    assert viewer.executed == []  # refused before reaching MySQL


def test_viewer_restriction_through_mcp_tool_call(viewer):
    with pytest.raises(ToolError, match="restricted"):
        anyio.run(server.mcp.call_tool, "run_query", {"sql": "SELECT * FROM deals"})


def test_viewer_can_query_other_tables_with_the_viewer_account(viewer):
    result = server.run_query("SELECT COUNT(*) AS n FROM companies WHERE name <> 'deals'")
    assert result["rows"] == [{"n": 1}]
    assert viewer.executed[-1].startswith("SELECT COUNT(*)")


def test_mysql_access_denied_becomes_a_restricted_message(monkeypatch):
    """If something slips past the name check, the Viewer MySQL account still refuses."""
    import pymysql
    denied = pymysql.err.OperationalError(1142, "SELECT command denied to user 'assistant_viewer'@'localhost' "
                                                "for table 'deals'")
    monkeypatch.setattr(server, "ROLE", "viewer")
    monkeypatch.setattr(server, "viewer_connection", lambda: FakeConnection(error=denied))
    with pytest.raises(ToolError, match="restricted for the Viewer role"):
        server.run_query("SELECT * FROM v_something")


def test_viewer_cannot_describe_deals(viewer, monkeypatch):
    monkeypatch.setattr(server, "_table_names", lambda: ["companies"])
    for name in ("deals", "DEALS", "`deals`"):
        with pytest.raises(ToolError, match="restricted"):
            server.describe_table(name)


def test_viewer_table_list_hides_deals_and_says_why(viewer, monkeypatch):
    rows = [{"name": n} for n in ["assets", "companies", "deals", "financials", "quarterly_financials"]]
    monkeypatch.setattr(server, "_fetch", lambda *a, **k: (["name"], rows, False))
    result = server.list_tables()
    assert result == {"tables": ["assets", "companies", "financials", "quarterly_financials"],
                      "restricted_for_your_role": ["deals"]}


def test_analyst_queries_deals_with_the_full_reader_account(monkeypatch):
    conn = FakeConnection()
    monkeypatch.setattr(server, "reader_connection", lambda: conn)  # viewer_connection still fails
    server.run_query("SELECT * FROM deals")
    assert conn.executed[-1] == "SELECT * FROM deals"


def test_unknown_role_is_refused():
    with pytest.raises(SystemExit):
        server.main(["--role", "admin"])


def test_role_tables_match_the_schema():
    """Every table is either granted to Viewers or restricted for them, never neither."""
    import re
    from roles import RESTRICTED_TABLES, VIEWER_TABLES
    schema = (Path(__file__).resolve().parent.parent / "data" / "schema.sql").read_text(encoding="utf-8")
    tables = set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?`?(\w+)`?", schema, re.IGNORECASE))
    assert tables == set(VIEWER_TABLES) | RESTRICTED_TABLES["viewer"]
    assert not set(VIEWER_TABLES) & RESTRICTED_TABLES["viewer"]
