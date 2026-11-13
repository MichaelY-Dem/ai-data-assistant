"""Sign-in, tokens, roles and rate limiting. Claude, MySQL and Blob Storage are faked."""
from __future__ import annotations

import datetime as dt
import re

import jwt
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import backend.main as main
from backend import auth
from backend.auth import Role, User
from backend.rate_limit import RateLimiter
from conftest import TEST_AUTH_ENV, TEST_PASSWORDS


@pytest.fixture
def client(monkeypatch):
    async def fake_ask(question, role):
        return f"answer for {role.value}", []

    monkeypatch.setattr(main, "ask_claude", fake_ask)
    monkeypatch.setattr(main, "safe_write_query_log", lambda **row: None)
    monkeypatch.setattr(main, "fetch_companies", lambda: [
        {"company_code": "CO-048", "name": "Alder Power Group", "sector": "energy", "status": "active"}])
    return TestClient(main.app)


def login(client, username, password):
    return client.post("/login", json={"username": username, "password": password})


# --------------------------------------------------------------------------- #
# Sign-in
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("role", ["analyst", "viewer"])
def test_login_returns_a_token_that_works(client, role):
    username = TEST_AUTH_ENV[f"{role.upper()}_USERNAME"]
    r = login(client, username, TEST_PASSWORDS[role])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["user"] == {"username": username, "display_name": TEST_AUTH_ENV[f"{role.upper()}_DISPLAY_NAME"],
                            "role": role}

    me = client.get("/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200 and me.json()["role"] == role

    claims = jwt.decode(body["access_token"], options={"verify_signature": False})
    assert claims["sub"] == username and claims["role"] == role
    assert 59 * 60 <= claims["exp"] - claims["iat"] <= 60 * 60


@pytest.mark.parametrize("username,password", [
    ("ana", "wrong-password"),
    ("nobody", "analyst-test-password"),
    ("ana", "viewer-test-password"),      # another user's password
    ("ANA", "analyst-test-password"),     # usernames are exact
    ("ana", "a" * 100),                   # longer than bcrypt's 72 bytes
])
def test_login_rejects_bad_credentials(client, username, password):
    r = login(client, username, password)
    assert r.status_code == 401
    assert r.json()["detail"] == "Wrong username or password."  # same message either way
    assert "access_token" not in r.text


def test_login_validates_input(client):
    assert client.post("/login", json={"username": "ana"}).status_code == 422
    assert login(client, "", "x").status_code == 422


def test_login_is_rate_limited_per_client(client):
    for _ in range(main.LOGIN_LIMITER.limit):
        assert login(client, "ana", "wrong").status_code == 401
    r = login(client, "ana", TEST_PASSWORDS["analyst"])  # even the right password is refused now
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1


def test_missing_configuration_fails_closed(client, monkeypatch, analyst_headers):
    monkeypatch.delenv("JWT_SECRET")
    assert login(client, "ana", TEST_PASSWORDS["analyst"]).status_code == 503
    assert client.get("/me", headers=analyst_headers).status_code == 503
    assert client.get("/health").json()["sign_in_configured"] is False

    monkeypatch.setenv("JWT_SECRET", "too-short")
    assert login(client, "ana", TEST_PASSWORDS["analyst"]).status_code == 503


def test_password_hash_helper_round_trips():
    h = auth.hash_password("correct horse battery staple")
    assert h.startswith("$2b$12$")
    with pytest.raises(ValueError):
        auth.hash_password("x" * 73)


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #

def _sign(claims: dict, secret: str = TEST_AUTH_ENV["JWT_SECRET"], alg: str = "HS256") -> str:
    now = dt.datetime.now(dt.timezone.utc)
    base = {"sub": "ana", "role": "analyst", "iss": auth.ISSUER, "aud": auth.AUDIENCE,
            "iat": now, "exp": now + dt.timedelta(minutes=5)}
    return jwt.encode({**base, **claims}, secret, algorithm=alg)


def _none_alg_token() -> str:
    import base64
    import json

    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    return f"{b64({'alg': 'none', 'typ': 'JWT'})}." + b64(
        {"sub": "ana", "iss": auth.ISSUER, "aud": auth.AUDIENCE, "iat": now, "exp": now + 300}) + "."


@pytest.mark.parametrize("token,why", [
    ("not-a-jwt", "garbage"),
    (_sign({}, secret="another-secret-" + "y" * 40), "wrong signing key"),
    (_sign({"exp": dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)}), "expired"),
    (_sign({"aud": "some-other-api"}), "wrong audience"),
    (_sign({"iss": "someone-else"}), "wrong issuer"),
    (_sign({"sub": "mallory"}), "user not in .env"),
    (_none_alg_token(), "alg=none"),
])
def test_bad_tokens_are_rejected(client, token, why):
    r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401, why


def test_expired_token_says_so(client):
    token = _sign({"exp": dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)})
    assert "expired" in client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["detail"]


def test_role_comes_from_env_not_from_the_token(client):
    # A token for the viewer that claims to be an analyst still gets the Viewer role.
    token = _sign({"sub": "vic", "role": "analyst"})
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/me", headers=headers).json()["role"] == "viewer"
    assert client.get("/documents", headers=headers).status_code == 403


def test_removing_a_user_revokes_their_tokens(client, monkeypatch, viewer_headers):
    assert client.get("/me", headers=viewer_headers).status_code == 200
    monkeypatch.delenv("VIEWER_USERNAME")
    assert client.get("/me", headers=viewer_headers).status_code == 401


# --------------------------------------------------------------------------- #
# Every endpoint except /health and /login needs a token
# --------------------------------------------------------------------------- #

def _api_routes():
    for route in main.app.routes:
        if isinstance(route, APIRoute) and route.include_in_schema:
            for method in route.methods:
                yield method, route.path


def test_public_paths_are_only_health_and_login():
    assert main.PUBLIC_PATHS == {"/health", "/login"}


@pytest.mark.parametrize("method,path", sorted(_api_routes()))
def test_every_other_endpoint_requires_a_token(client, method, path):
    if path in main.PUBLIC_PATHS:
        pytest.skip("public")
    url = re.sub(r"\{[^}]+\}", "x", path)
    for headers in ({}, {"Authorization": "Bearer junk"}, {"Authorization": "Basic YW5hOnB3"}):
        r = client.request(method, url, headers=headers)
        assert r.status_code == 401, (method, path, headers, r.status_code)


# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #

def test_viewer_can_chat_and_list_companies(client, viewer_headers):
    r = client.post("/chat", json={"question": "Deals?"}, headers=viewer_headers)
    assert r.status_code == 200 and r.json()["answer"] == "answer for viewer"
    assert client.get("/companies", headers=viewer_headers).status_code == 200


@pytest.mark.parametrize("method,path", [
    ("GET", "/documents"),
    ("POST", "/documents/upload"),
    ("POST", "/documents/" + "0" * 32 + "/summarize"),
    ("POST", "/companies/CO-048/one-page-summary"),
])
def test_documents_are_analyst_only(client, viewer_headers, method, path):
    r = client.request(method, path, headers=viewer_headers)
    assert r.status_code == 403
    assert "Analyst role only" in r.json()["detail"]


def test_viewer_agent_gets_viewer_mcp_server_and_role_note():
    opts = main.agent_options(main.load_system_prompt() + main.ROLE_NOTES[Role.VIEWER], role=Role.VIEWER)
    assert opts.mcp_servers["portfolio"]["args"][-2:] == ["--role", "viewer"]
    assert "restricted for this role" in opts.system_prompt
    assert opts.tools == [] and opts.setting_sources == []

    analyst = main.agent_options("x", role=Role.ANALYST)
    assert analyst.mcp_servers["portfolio"]["args"][-2:] == ["--role", "analyst"]


def test_database_agent_without_a_role_is_refused():
    with pytest.raises(ValueError):
        main.agent_options("x", role=None)
    with pytest.raises(ValueError):
        main.agent_options("x", role="analyst")  # a plain string isn't a verified Role


def test_ask_claude_uses_the_role(monkeypatch):
    import anyio
    seen = {}

    async def fake_run(prompt, options):
        seen["args"] = options.mcp_servers["portfolio"]["args"]
        seen["system"] = options.system_prompt
        return "ok", []

    monkeypatch.setattr(main, "run_agent", fake_run)
    anyio.run(main.ask_claude, "List the deals", Role.VIEWER)
    assert seen["args"][-1] == "viewer" and "Viewer role" in seen["system"]


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #

def test_chat_is_limited_to_20_per_minute_per_user(client, analyst_headers, viewer_headers):
    for i in range(20):
        r = client.post("/chat", json={"question": f"q{i}"}, headers=analyst_headers)
        assert r.status_code == 200, i
    r = client.post("/chat", json={"question": "one too many"}, headers=analyst_headers)
    assert r.status_code == 429
    assert 1 <= int(r.headers["retry-after"]) <= 60
    assert "20 per minute" in r.json()["detail"]
    # Other users have their own allowance.
    assert client.post("/chat", json={"question": "hi"}, headers=viewer_headers).status_code == 200


def test_unauthenticated_requests_dont_use_up_a_users_allowance(client, analyst_headers):
    for _ in range(25):
        assert client.post("/chat", json={"question": "x"}).status_code == 401
    assert client.post("/chat", json={"question": "x"}, headers=analyst_headers).status_code == 200


def test_rate_limiter_window_slides():
    now = [1000.0]
    limiter = RateLimiter(limit=2, window_s=60, clock=lambda: now[0])
    assert limiter.hit("a") is None
    now[0] += 30
    assert limiter.hit("a") is None
    assert limiter.hit("a") == pytest.approx(30)   # first hit expires in 30 s
    assert limiter.hit("b") is None                 # separate key
    now[0] += 30.001
    assert limiter.hit("a") is None                 # first hit has left the window
    assert limiter.hit("a") is not None


def test_user_model_is_what_endpoints_see():
    # The seam for Entra ID: endpoints only depend on this shape.
    assert set(User.model_fields) == {"username", "display_name", "role"}
    assert {r.value for r in Role} == {"analyst", "viewer"}
