"""Tests for the FastAPI backend. Claude and MySQL are replaced with fakes."""
from __future__ import annotations

from fastapi.testclient import TestClient

import backend.main as main
from backend.auth import Role


def _client(monkeypatch, answer="42 companies.", tools=("run_query",), fail=None):
    logged: list[dict] = []
    asked: list[tuple[str, Role]] = []

    async def fake_ask(question, role):
        asked.append((question, role))
        if fail:
            raise fail
        return answer, list(tools)

    monkeypatch.setattr(main, "ask_claude", fake_ask)
    monkeypatch.setattr(main, "safe_write_query_log", lambda **row: logged.append(row))
    client = TestClient(main.app)
    client.asked = asked
    return client, logged


def test_health_is_public():
    r = TestClient(main.app).get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["system_prompt_found"] and body["mcp_server_found"]
    assert body["sign_in_configured"] is True
    assert "sonnet" in body["model"]


def test_chat_returns_answer_and_logs_who_asked(monkeypatch, analyst_headers):
    client, logged = _client(monkeypatch)
    r = client.post("/chat", json={"question": "  How many companies?  "}, headers=analyst_headers)
    assert r.status_code == 200
    assert r.json()["answer"] == "42 companies."
    assert r.json()["tools_used"] == ["run_query"]
    assert client.asked == [("How many companies?", Role.ANALYST)]
    assert len(logged) == 1
    row = logged[0]
    assert row["question"] == "How many companies?"
    assert row["answer_length"] == len("42 companies.")
    assert row["tools_used"] == ["run_query"]
    assert row["status"] == "ok"
    assert row["duration_ms"] >= 0
    assert row["username"] == "ana" and row["user_role"] == "analyst"


def test_chat_is_not_marked_as_eval_by_default(monkeypatch, analyst_headers):
    client, logged = _client(monkeypatch)
    client.post("/chat", json={"question": "How many companies?"}, headers=analyst_headers)
    assert logged[0]["is_eval"] is False


def test_eval_header_marks_the_log_row(monkeypatch, analyst_headers):
    client, logged = _client(monkeypatch)
    for value, expected in [("true", True), ("TRUE", True), ("1", True), ("false", False), ("", False)]:
        r = client.post("/chat", json={"question": "Q"}, headers={**analyst_headers, "X-Eval-Run": value})
        assert r.status_code == 200
        assert logged[-1]["is_eval"] is expected, value
    # the header only tags the log row: the question and role reach Claude unchanged
    assert all(asked == ("Q", Role.ANALYST) for asked in client.asked)


def test_chat_passes_the_viewer_role_from_the_token(monkeypatch, viewer_headers):
    client, logged = _client(monkeypatch)
    assert client.post("/chat", json={"question": "Deals?"}, headers=viewer_headers).status_code == 200
    assert client.asked == [("Deals?", Role.VIEWER)]
    assert logged[0]["username"] == "vic" and logged[0]["user_role"] == "viewer"


def test_chat_failure_is_logged_and_returns_502(monkeypatch, analyst_headers):
    client, logged = _client(monkeypatch, fail=RuntimeError("boom"))
    r = client.post("/chat", json={"question": "Anything"}, headers=analyst_headers)
    assert r.status_code == 502
    assert logged[0]["status"] == "error" and logged[0]["answer_length"] == 0


def test_chat_rejects_blank_question(monkeypatch, analyst_headers):
    client, logged = _client(monkeypatch)
    assert client.post("/chat", json={"question": "   "}, headers=analyst_headers).status_code == 422
    assert client.post("/chat", json={}, headers=analyst_headers).status_code == 422
    assert logged == []


def test_chat_without_token_never_reaches_claude(monkeypatch):
    client, logged = _client(monkeypatch)
    r = client.post("/chat", json={"question": "How many companies?"})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert client.asked == [] and logged == []


def test_system_prompt_is_loaded_from_file():
    text = main.load_system_prompt()
    assert text == main.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    opts = main.agent_options(text, role=Role.ANALYST)
    assert opts.system_prompt == text
    assert opts.tools == []
    assert opts.allowed_tools == [
        "mcp__portfolio__list_tables", "mcp__portfolio__describe_table", "mcp__portfolio__run_query",
    ]
