"""Tests for the FastAPI backend. Claude and MySQL are replaced with fakes."""
from __future__ import annotations

from fastapi.testclient import TestClient

import backend.main as main


def _client(monkeypatch, answer="42 companies.", tools=("run_query",), fail=None):
    logged: list[dict] = []

    async def fake_ask(question):
        if fail:
            raise fail
        return answer, list(tools)

    monkeypatch.setattr(main, "ask_claude", fake_ask)
    monkeypatch.setattr(main, "safe_write_query_log", lambda **row: logged.append(row))
    return TestClient(main.app), logged


def test_health():
    r = TestClient(main.app).get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["system_prompt_found"] and body["mcp_server_found"]
    assert "sonnet" in body["model"]


def test_chat_returns_answer_and_logs(monkeypatch):
    client, logged = _client(monkeypatch)
    r = client.post("/chat", json={"question": "  How many companies?  "})
    assert r.status_code == 200
    assert r.json()["answer"] == "42 companies."
    assert r.json()["tools_used"] == ["run_query"]
    assert len(logged) == 1
    row = logged[0]
    assert row["question"] == "How many companies?"
    assert row["answer_length"] == len("42 companies.")
    assert row["tools_used"] == ["run_query"]
    assert row["status"] == "ok"
    assert row["duration_ms"] >= 0


def test_chat_failure_is_logged_and_returns_502(monkeypatch):
    client, logged = _client(monkeypatch, fail=RuntimeError("boom"))
    r = client.post("/chat", json={"question": "Anything"})
    assert r.status_code == 502
    assert logged[0]["status"] == "error" and logged[0]["answer_length"] == 0


def test_chat_rejects_blank_question(monkeypatch):
    client, logged = _client(monkeypatch)
    assert client.post("/chat", json={"question": "   "}).status_code == 422
    assert client.post("/chat", json={}).status_code == 422
    assert logged == []


def test_system_prompt_is_loaded_from_file():
    text = main.load_system_prompt()
    assert text == main.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    opts = main.agent_options(text)
    assert opts.system_prompt == text
    assert opts.tools == []
    assert opts.allowed_tools == [
        "mcp__portfolio__list_tables", "mcp__portfolio__describe_table", "mcp__portfolio__run_query",
    ]
