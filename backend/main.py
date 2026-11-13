"""FastAPI backend: ask Claude questions about the portfolio database.

Claude runs through the Claude Agent SDK, signed in with your `claude login`
credentials (no API key), with the project's MCP server (mcp_server/server.py)
as its only tool source. The system prompt is read from prompts/system.md.

Endpoints:
    POST /chat    {"question": "..."}  ->  {"answer": "...", "tools_used": [...], "duration_ms": ...}
    GET  /health  liveness check

Start it from the project root:
    .venv\\Scripts\\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sys
import time
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "data"))  # for db_config (also loads .env)

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    ResultMessage,
    ToolUseBlock,
    query,
)

from backend.query_log import safe_write_query_log  # noqa: E402

log = logging.getLogger("backend")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

MODEL = "claude-sonnet-5-5"
SYSTEM_PROMPT_PATH = PROJECT_ROOT / "prompts" / "system.md"
MCP_SERVER_PATH = PROJECT_ROOT / "mcp_server" / "server.py"
MCP_SERVER_NAME = "portfolio"
MCP_TOOLS = ["list_tables", "describe_table", "run_query"]
MAX_TURNS = 20               # cap on Claude <-> tool round trips per question
REQUEST_TIMEOUT_S = 240      # give up on a single question after this long

# Use the `claude login` subscription credentials, never an API key: the Claude
# Code CLI the SDK starts would prefer ANTHROPIC_API_KEY if it were set.
if os.environ.pop("ANTHROPIC_API_KEY", None):
    log.info("Ignoring ANTHROPIC_API_KEY: this backend uses your `claude login` credentials.")

app = FastAPI(
    title="Portfolio analyst assistant",
    description="Ask Claude questions about the infrastructure portfolio database.",
    version="0.1.0",
)


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000,
                          examples=["Which energy companies missed their revenue target in Q3 2026?"])


class ChatResponse(BaseModel):
    answer: str
    tools_used: list[str]
    duration_ms: int


def load_system_prompt() -> str:
    """Read prompts/system.md on every request, so edits apply without a restart."""
    try:
        return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Cannot read {SYSTEM_PROMPT_PATH.name}: {exc}") from exc


def agent_options(system_prompt: str) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=MODEL,
        system_prompt=system_prompt,
        mcp_servers={
            MCP_SERVER_NAME: {
                "type": "stdio",
                "command": sys.executable,
                "args": [str(MCP_SERVER_PATH)],
            }
        },
        strict_mcp_config=True,      # only our MCP server, not any from user/project config
        tools=[],                    # no built-in tools (no Bash, file access, web, ...)
        allowed_tools=[f"mcp__{MCP_SERVER_NAME}__{name}" for name in MCP_TOOLS],
        permission_mode="dontAsk",   # anything not in allowed_tools is denied, never prompted
        setting_sources=[],          # ignore local Claude Code settings, hooks and CLAUDE.md
        max_turns=MAX_TURNS,
        cwd=str(PROJECT_ROOT),
    )


async def ask_claude(question: str) -> tuple[str, list[str]]:
    """Run one question through the agent. Returns (answer, tool names in call order)."""
    tools_used: list[str] = []
    result: ResultMessage | None = None
    async for message in query(prompt=question, options=agent_options(load_system_prompt())):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    tools_used.append(block.name.removeprefix(f"mcp__{MCP_SERVER_NAME}__"))
        elif isinstance(message, ResultMessage):
            result = message

    if result is None:
        raise RuntimeError("Claude finished without a result.")
    if result.is_error or not result.result:
        raise RuntimeError(f"Claude run failed ({result.subtype}): {result.errors or result.result}")
    return result.result, tools_used


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question must not be blank")

    asked_at = dt.datetime.now(dt.timezone.utc)
    started = time.perf_counter()
    answer, tools_used, error = "", [], None
    try:
        with anyio.fail_after(REQUEST_TIMEOUT_S):
            answer, tools_used = await ask_claude(question)
    except TimeoutError:
        error = HTTPException(status_code=504, detail=f"Claude did not answer within {REQUEST_TIMEOUT_S} s.")
    except (ClaudeSDKError, RuntimeError) as exc:
        log.exception("Claude request failed")
        error = HTTPException(status_code=502, detail=f"Claude request failed: {exc}")
    duration_ms = round((time.perf_counter() - started) * 1000)

    await anyio.to_thread.run_sync(lambda: safe_write_query_log(
        asked_at=asked_at,
        question=question,
        answer_length=len(answer),
        tools_used=tools_used,
        duration_ms=duration_ms,
        status="error" if error else "ok",
    ))

    if error:
        raise error
    return ChatResponse(answer=answer, tools_used=tools_used, duration_ms=duration_ms)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "model": MODEL,
        "system_prompt_found": SYSTEM_PROMPT_PATH.is_file(),
        "mcp_server_found": MCP_SERVER_PATH.is_file(),
    }
