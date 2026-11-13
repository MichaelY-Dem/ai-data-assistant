"""Accuracy evaluation: ask the running backend 25 questions and check the answers against MySQL.

Steps:
  1. Recompute every expected answer from MySQL (build_expected.py), so they match the live data.
  2. Sign in to the backend as the Analyst (EVAL_USERNAME / EVAL_PASSWORD from .env). The one
     Viewer-restricted question signs in as the Viewer (EVAL_VIEWER_USERNAME /
     EVAL_VIEWER_PASSWORD); if those aren't set, that question is skipped.
  3. POST each question to /chat, waiting 3 seconds between questions so the run stays well under
     the 20-per-minute rate limit. A 429 is retried after its Retry-After.
  4. Grade each answer: numbers must appear within a small tolerance, names must be present (in
     order, for rankings), the missing-data question must say the data isn't there, and the
     restricted question must say so without leaking the real figure.
  5. Print a results table and the overall score, and write docs/eval-results.md.

The backend must already be running (see README). Every request carries the header
X-Eval-Run: true, so its query_log row has is_eval = 1 and the usage export doesn't count it as
time saved.

Usage (from the project root):
    .venv\\Scripts\\python.exe tests\\eval\\run_eval.py
    .venv\\Scripts\\python.exe tests\\eval\\run_eval.py --only Q15,Q24     # a few questions
    .venv\\Scripts\\python.exe tests\\eval\\run_eval.py --base-url http://127.0.0.1:8001
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

EVAL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_DIR.parents[1]
RESULTS_PATH = PROJECT_ROOT / "docs" / "eval-results.md"
sys.path.insert(0, str(EVAL_DIR))

from build_expected import QUESTIONS_PATH, build  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DELAY_S = 3.0             # pause between questions (rate limit: 20 /chat per minute per user)
HTTP_TIMEOUT_S = 300      # the backend itself gives up on Claude after 240 s
MAX_RATE_LIMIT_RETRIES = 3

# A number in the answer matches an expected one if it is within max(ABS_TOL, REL_TOL x expected).
# ABS_TOL covers rounding to one decimal place ("USD 454.6m" for 454.5875); REL_TOL covers large
# amounts rounded to whole millions. An amount written in billions ("USD 131.0bn" for 131,035.1m)
# may be off by up to BN_TOL million, since one decimal of a billion is USD 100m.
ABS_TOL = 0.051
REL_TOL = 0.0005
BN_TOL = 51.0

NO_DATA_PATTERNS = [
    r"\bno data\b", r"\bno (?:\w+ )?(?:figures|records|rows|financials|results)\b",
    r"\bnot (?:available|covered|included|in the database)\b", r"\bunavailable\b",
    r"\b(?:isn't|is not|aren't|are not) (?:any |available|covered|included)",
    r"\b(?:doesn't|does not|don't|do not) (?:have|hold|contain|include|cover)\b",
    r"\bonly (?:covers|goes back|has|holds|contains|starts)\b", r"\bnothing (?:for|in|before)\b",
]
RESTRICTED_PATTERNS = [
    r"\brestricted\b", r"\bnot (?:permitted|authori[sz]ed|allowed)\b", r"\baccess denied\b",
    r"\b(?:don't|do not|doesn't|does not) have (?:access|permission)\b",
]


# --------------------------------------------------------------------------- #
# Backend calls (standard library only)
# --------------------------------------------------------------------------- #

class BackendError(Exception):
    def __init__(self, status: int, detail: str, retry_after: float | None = None):
        super().__init__(f"HTTP {status}: {detail}")
        self.status, self.detail, self.retry_after = status, detail, retry_after


def http_json(method: str, url: str, body: dict | None = None, token: str | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Eval-Run", "true")  # logged as is_eval = 1, so usage reports can leave it out
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except (ValueError, AttributeError):
            detail = exc.reason
        retry = exc.headers.get("Retry-After")
        raise BackendError(exc.code, str(detail), float(retry) if retry else None) from None


def env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def sign_in(base_url: str, user_var: str, pw_var: str, expect_role: str) -> str:
    username, password = env(user_var), env(pw_var)
    if not username or not password:
        raise SystemExit(f"Set {user_var} and {pw_var} in .env (see README, 'Accuracy evaluation').")
    try:
        resp = http_json("POST", f"{base_url}/login", {"username": username, "password": password})
    except BackendError as exc:
        raise SystemExit(f"Sign-in as {user_var} failed: {exc}") from None
    role = resp["user"]["role"]
    if role != expect_role:
        raise SystemExit(f"{user_var} signed in as role '{role}', but the evaluation needs '{expect_role}'.")
    return resp["access_token"]


# --------------------------------------------------------------------------- #
# Grading
# --------------------------------------------------------------------------- #

NUMBER_RE = re.compile(
    r"(?<![\w.,])(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)(?:\s*(bn|billion)\b)?",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    """Lower case, Markdown emphasis removed, underscores as spaces (data_centers = data centers)."""
    text = re.sub(r"[*`\\]", "", text).replace("_", " ").lower()
    return re.sub(r"\s+", " ", text)


def numbers_in(text: str) -> list[tuple[float, bool]]:
    """Every number in the text as (value in USD millions where it's an amount, written in bn?)."""
    found = []
    for digits, billions in NUMBER_RE.findall(text):
        value = float(digits.replace(",", ""))
        found.append((value * 1000, True) if billions else (value, False))
    return found


# Small counts are often written as words ("Two deals were terminated"). Words are only read when the
# expected value is a whole number from 1 to 20, and only ever match that exact value.
NUMBER_WORDS = {w: i for i, w in enumerate(
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen"
    " sixteen seventeen eighteen nineteen twenty".split(), start=1)}
NUMBER_WORD_RE = re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r")\b", re.IGNORECASE)
MAX_WORD_NUMBER = 20


def number_words_in(text: str) -> set[int]:
    """Values of the number words one..twenty that appear in the text."""
    return {NUMBER_WORDS[w.lower()] for w in NUMBER_WORD_RE.findall(text)}


def matches(expected: float, found: list[tuple[float, bool]], words: set[int] = frozenset()) -> bool:
    tol = max(ABS_TOL, REL_TOL * abs(expected))
    if any(abs(v - expected) <= (max(tol, BN_TOL) if bn else tol) for v, bn in found):
        return True
    small_whole = float(expected).is_integer() and 1 <= expected <= MAX_WORD_NUMBER
    return small_whole and int(expected) in words


def fmt(x: float) -> str:
    return f"{x:,.4f}".rstrip("0").rstrip(".")


def grade(q: dict, answer: str) -> tuple[bool, str]:
    """(passed, short reason) for one answer."""
    check, exp = q["check"], q["expected"]
    text = normalize(answer)
    found = numbers_in(answer)
    words = number_words_in(answer)
    problems = []

    if check in ("number", "numbers", "names_numbers"):
        missing = [fmt(n) for n in exp["numbers"] if not matches(n, found, words)]
        if missing:
            problems.append("missing numbers: " + ", ".join(missing))

    if check in ("names", "names_numbers"):
        names = [normalize(n) for n in exp["names"]]
        missing = [exp["names"][i] for i, n in enumerate(names) if n not in text]
        if missing:
            problems.append("missing names: " + ", ".join(missing))
        elif q.get("ordered"):
            positions = [text.index(n) for n in names]
            if positions != sorted(positions):
                problems.append("names in the wrong order")

    if check == "no_data":
        if not any(re.search(p, text) for p in NO_DATA_PATTERNS):
            problems.append("didn't say the data is missing")

    if check == "restricted":
        if not any(re.search(p, text) for p in RESTRICTED_PATTERNS):
            problems.append("didn't say the data is restricted")
        leaked = [fmt(n) for n in exp["must_not_contain"] if matches(n, found, words)]
        if leaked:
            problems.append("LEAKED restricted figure: " + ", ".join(leaked))

    if problems:
        return False, "; ".join(problems)
    return True, {"no_data": "said data is missing", "restricted": "refused, nothing leaked"}.get(check, "all values found")


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #

@dataclass
class Result:
    q: dict
    role: str
    outcome: str = "SKIP"          # PASS, FAIL, ERROR, SKIP
    detail: str = ""
    answer: str = ""
    seconds: float = 0.0
    tools: list[str] = field(default_factory=list)


def ask(base_url: str, token: str, question: str) -> dict:
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        try:
            return http_json("POST", f"{base_url}/chat", {"question": question}, token)
        except BackendError as exc:
            if exc.status != 429 or attempt == MAX_RATE_LIMIT_RETRIES:
                raise
            wait = (exc.retry_after or 10) + 1
            print(f"      rate limited, waiting {wait:.0f} s ...", flush=True)
            time.sleep(wait)
    raise AssertionError("unreachable")


def run(base_url: str, questions: list[dict], delay: float) -> list[Result]:
    try:
        http_json("GET", f"{base_url}/health")
    except (urllib.error.URLError, OSError, BackendError) as exc:
        raise SystemExit(f"The backend isn't reachable at {base_url} ({exc}). Start it first (see README).")

    tokens = {"analyst": sign_in(base_url, "EVAL_USERNAME", "EVAL_PASSWORD", "analyst")}
    if any(q.get("as_role") == "viewer" for q in questions):
        if env("EVAL_VIEWER_USERNAME") and env("EVAL_VIEWER_PASSWORD"):
            tokens["viewer"] = sign_in(base_url, "EVAL_VIEWER_USERNAME", "EVAL_VIEWER_PASSWORD", "viewer")
        else:
            print("EVAL_VIEWER_USERNAME / EVAL_VIEWER_PASSWORD not set: the Viewer question will be skipped.")

    results = []
    for i, q in enumerate(questions):
        role = q.get("as_role", "analyst")
        r = Result(q, role)
        results.append(r)
        print(f"[{i + 1:>2}/{len(questions)}] {q['id']} ({role}) {q['question']}", flush=True)
        if role not in tokens:
            r.detail = f"no {role} credentials in .env"
            print(f"      SKIP  {r.detail}")
            continue
        if i:
            time.sleep(delay)
        start = time.monotonic()
        try:
            try:
                resp = ask(base_url, tokens[role], q["question"])
            except BackendError as exc:
                if exc.status != 401:
                    raise
                # token expired during a long run: sign in again once
                user_var, pw_var = (("EVAL_USERNAME", "EVAL_PASSWORD") if role == "analyst"
                                    else ("EVAL_VIEWER_USERNAME", "EVAL_VIEWER_PASSWORD"))
                tokens[role] = sign_in(base_url, user_var, pw_var, role)
                resp = ask(base_url, tokens[role], q["question"])
        except (BackendError, urllib.error.URLError, OSError) as exc:
            r.outcome, r.detail = "ERROR", str(exc)
        else:
            r.answer, r.tools = resp["answer"], resp.get("tools_used", [])
            passed, r.detail = grade(q, r.answer)
            r.outcome = "PASS" if passed else "FAIL"
        r.seconds = time.monotonic() - start
        print(f"      {r.outcome}  {r.detail}  ({r.seconds:.1f} s)", flush=True)
    return results


def score(results: list[Result]) -> tuple[int, int]:
    graded = [r for r in results if r.outcome != "SKIP"]
    return sum(r.outcome == "PASS" for r in graded), len(graded)


def print_table(results: list[Result]) -> None:
    rows = [("ID", "Category", "Role", "Result", "Secs", "Detail")]
    rows += [(r.q["id"], r.q["category"], r.role, r.outcome, f"{r.seconds:.1f}", r.detail[:70])
             for r in results]
    widths = [max(len(row[i]) for row in rows) for i in range(5)]
    print()
    for n, row in enumerate(rows):
        print("  ".join(c.ljust(w) for c, w in zip(row[:5], widths)) + "  " + row[5])
        if n == 0:
            print("  ".join("-" * w for w in widths) + "  " + "-" * 6)
    passed, graded = score(results)
    skipped = len(results) - graded
    pct = 100 * passed / graded if graded else 0
    print(f"\nScore: {passed}/{graded} correct ({pct:.0f}%)" + (f", {skipped} skipped" if skipped else ""))


def md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def write_markdown(results: list[Result], base_url: str, started: dt.datetime, subset: bool) -> None:
    passed, graded = score(results)
    skipped = len(results) - graded
    pct = 100 * passed / graded if graded else 0
    lines = [
        "# Accuracy evaluation results",
        "",
        f"**Date:** {started:%Y-%m-%d %H:%M} (local time)  ",
        f"**Score:** {passed}/{graded} correct ({pct:.0f}%)"
        + (f", {skipped} skipped" if skipped else "") + "  ",
        f"**Backend:** {base_url}  ",
        f"**Questions:** `tests/eval/questions.json`"
        + (" (subset run with --only)" if subset else "")
        + "; expected answers recomputed from MySQL at the start of the run.",
        "",
        "Numbers must match within max(0.051, 0.05% of the expected value), or USD 51m when written in"
        " billions; whole-number answers of 20 or less may also be written as words (\"two\"); names must be present"
        " (in order for rankings). Generated by `tests/eval/run_eval.py`.",
        "",
        "## Grader changes",
        "",
        "- 2026-11-13: the first run scored 24/25 because the grader missed a correct answer written as"
        " \"Two\" (Q23: 2 terminated deals, USD 531.0m on the dataset of the time). The grader was fixed"
        " to read the number words one to twenty when the expected value is a whole number of 20 or less"
        " (number tolerance unchanged), and the full suite was rerun.",
        "- 2026-11-13: the fake data was regenerated with realistic values per sector (EBITDA margins at"
        " or below ~60%, leverage of 4-8.5x EBITDA, commissioning dates no earlier than each technology,"
        " revenue, book and deal values scaled to asset capacity). Same companies and the same three"
        " Q3 2026 energy misses; expected answers rebuilt from MySQL. No grader change.",
        "",
        "## By category",
        "",
        "| Category | Correct |",
        "|---|---|",
    ]
    by_cat: dict[str, list[Result]] = defaultdict(list)
    for r in results:
        by_cat[r.q["category"]].append(r)
    for cat, rs in by_cat.items():
        p, g = score(rs)
        lines.append(f"| {cat} | {p}/{g} |")

    lines += ["", "## Results", "", "| ID | Category | Question | Role | Result | Seconds | Detail |",
              "|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.q['id']} | {r.q['category']} | {md_cell(r.q['question'])} | {r.role} | "
                     f"**{r.outcome}** | {r.seconds:.1f} | {md_cell(r.detail)} |")

    lines += ["", "## Answers", ""]
    for r in results:
        if not r.answer:
            continue
        exp = json.dumps(r.q["expected"], ensure_ascii=False)
        lines += [f"<details><summary>{r.q['id']} {r.outcome}: {md_cell(r.q['question'])}</summary>", "",
                  f"Expected: `{exp}`  ", f"Tools used: {', '.join(r.tools) or 'none'}", "",
                  r.answer.strip(), "", "</details>", ""]

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Saved {RESULTS_PATH.relative_to(PROJECT_ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default=env("EVAL_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--only", help="comma-separated question ids, e.g. Q01,Q15")
    parser.add_argument("--delay", type=float, default=DELAY_S, help="seconds between questions (default 3)")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    print("Computing expected answers from MySQL ...")
    questions = build(QUESTIONS_PATH, quiet=True)["questions"]
    if args.only:
        wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}
        unknown = wanted - {q["id"] for q in questions}
        if unknown:
            raise SystemExit(f"Unknown question ids: {', '.join(sorted(unknown))}")
        questions = [q for q in questions if q["id"] in wanted]

    started = dt.datetime.now()
    results = run(base_url, questions, args.delay)
    print_table(results)
    write_markdown(results, base_url, started, subset=bool(args.only))
    return 0 if all(r.outcome in ("PASS", "SKIP") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
