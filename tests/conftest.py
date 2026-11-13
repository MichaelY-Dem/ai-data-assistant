import os
import sys
from pathlib import Path

import bcrypt
import pytest

# The data scripts import each other as plain modules (they're run as scripts),
# so make data/ importable from the tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "data"))

# Test users. Low bcrypt cost keeps the tests fast; real hashes use cost 12.
TEST_PASSWORDS = {"analyst": "analyst-test-password", "viewer": "viewer-test-password"}
_HASHES = {role: bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4)).decode()
           for role, pw in TEST_PASSWORDS.items()}
TEST_AUTH_ENV = {
    "JWT_SECRET": "test-secret-" + "x" * 40,
    "JWT_EXPIRE_MINUTES": "60",
    "ANALYST_USERNAME": "ana",
    "ANALYST_PASSWORD_HASH": _HASHES["analyst"],
    "ANALYST_DISPLAY_NAME": "Ana Analyst",
    "VIEWER_USERNAME": "vic",
    "VIEWER_PASSWORD_HASH": _HASHES["viewer"],
    "VIEWER_DISPLAY_NAME": "Vic Viewer",
}


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: calls real Claude and MySQL; runs only when RUN_LIVE_TESTS=1 (see SECURITY.md)",
    )


def pytest_collection_modifyitems(config, items):
    if os.getenv("RUN_LIVE_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set RUN_LIVE_TESTS=1 to run (needs MySQL and `claude login`)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def test_auth_settings(monkeypatch):
    """Every test sees the same two demo users and JWT secret, whatever .env says."""
    for name, value in TEST_AUTH_ENV.items():
        monkeypatch.setenv(name, value)
    if "backend.main" in sys.modules:  # fresh rate-limit counters for each test
        main = sys.modules["backend.main"]
        main.CHAT_LIMITER.reset()
        main.LOGIN_LIMITER.reset()


def token_headers(role: str) -> dict[str, str]:
    """Authorization header with a valid token for the test analyst or viewer."""
    from backend.auth import Role, User, create_token

    user = User(
        username=TEST_AUTH_ENV[f"{role.upper()}_USERNAME"],
        display_name=TEST_AUTH_ENV[f"{role.upper()}_DISPLAY_NAME"],
        role=Role(role),
    )
    token, _ = create_token(user)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def analyst_headers():
    return token_headers("analyst")


@pytest.fixture
def viewer_headers():
    return token_headers("viewer")
