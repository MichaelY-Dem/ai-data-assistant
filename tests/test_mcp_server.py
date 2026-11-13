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
