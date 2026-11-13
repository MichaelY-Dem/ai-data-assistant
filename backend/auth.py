"""Sign-in and token checks. The only module that knows how users authenticate.

Today: two demo users defined in .env, a bcrypt password check, and our own HS256 JWTs
(PyJWT) signed with JWT_SECRET.

    JWT_SECRET               random string, at least 32 characters (signs the tokens)
    JWT_EXPIRE_MINUTES       token lifetime, default 60
    ANALYST_USERNAME         e.g. analyst
    ANALYST_PASSWORD_HASH    bcrypt hash, from: python -m backend.hash_password
    ANALYST_DISPLAY_NAME     optional, shown in the header (defaults to the username)
    VIEWER_USERNAME / VIEWER_PASSWORD_HASH / VIEWER_DISPLAY_NAME   same for the Viewer

Settings are read on every call, so a test can override them and nothing is cached at
import. A token is only accepted if its user still exists in .env, and the role always
comes from .env, so removing a user or changing a role takes effect at once.

The rest of the backend uses just three things from here: the `User` model, the
`current_user` FastAPI dependency, and `require_role(...)`. That is the seam.

Replacing this with Microsoft Entra ID later
--------------------------------------------
1. In Entra ID, register an app for this API, define two app roles (for example
   "Portfolio.Analyst" and "Portfolio.Viewer") and assign them to users or groups.
2. The frontend signs in with MSAL.js (@azure/msal-react) and sends the Entra access
   token as `Authorization: Bearer <token>`, exactly where our token goes today.
3. Replace `decode_token` below with Entra token validation:
       jwks = jwt.PyJWKClient(f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys")
       key = jwks.get_signing_key_from_jwt(token)
       claims = jwt.decode(token, key, algorithms=["RS256"], audience=API_CLIENT_ID,
                           issuer=f"https://login.microsoftonline.com/{TENANT_ID}/v2.0")
   then build the User from claims: username = claims["oid"] (or "preferred_username"),
   display_name = claims["name"], role from claims["roles"] (refuse a token with neither
   app role). Keep PyJWKClient as a module-level object so its key cache is reused.
4. Delete `authenticate`, `create_token`, `LoginRequest`/`LoginResponse`, the POST /login
   endpoint, backend/hash_password.py and the demo-user settings. Entra ID then also
   provides MFA, conditional access and account lockout.
Endpoints, the role checks, the rate limiter (keyed by username) and the MCP role hand-off
don't change, because they only see `User`.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from enum import Enum

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

log = logging.getLogger("backend.auth")

ALGORITHM = "HS256"
ISSUER = "portfolio-assistant"
AUDIENCE = "portfolio-assistant-api"
MIN_SECRET_LENGTH = 32
DEFAULT_EXPIRE_MINUTES = 60
MAX_PASSWORD_BYTES = 72  # bcrypt only looks at the first 72 bytes; longer is refused

# Checked against when the username is unknown, so a wrong username takes as long as a
# wrong password and response times don't reveal which usernames exist.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt(rounds=12))


class Role(str, Enum):
    ANALYST = "analyst"
    VIEWER = "viewer"

    @property
    def label(self) -> str:
        return self.value.capitalize()


class User(BaseModel):
    username: str
    display_name: str
    role: Role


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: dt.datetime
    user: User


class AuthNotConfigured(Exception):
    """Raised when .env is missing the auth settings. Mapped to HTTP 503."""


# --------------------------------------------------------------------------- #
# Settings (read on every call)
# --------------------------------------------------------------------------- #

def _secret() -> str:
    secret = os.getenv("JWT_SECRET", "")
    if len(secret) < MIN_SECRET_LENGTH:
        raise AuthNotConfigured(f"JWT_SECRET must be set in .env (at least {MIN_SECRET_LENGTH} characters).")
    return secret


def _expire_minutes() -> int:
    try:
        minutes = int(os.getenv("JWT_EXPIRE_MINUTES") or DEFAULT_EXPIRE_MINUTES)
    except ValueError:
        minutes = DEFAULT_EXPIRE_MINUTES
    return max(1, min(minutes, 24 * 60))


def _configured_users() -> dict[str, tuple[User, bytes]]:
    """username -> (User, bcrypt hash) for each demo user that is fully configured."""
    users: dict[str, tuple[User, bytes]] = {}
    for role in Role:
        prefix = role.name  # ANALYST / VIEWER
        username = (os.getenv(f"{prefix}_USERNAME") or "").strip()
        pw_hash = (os.getenv(f"{prefix}_PASSWORD_HASH") or "").strip()
        if not username or not pw_hash:
            continue
        if username in users:
            log.error("%s_USERNAME duplicates another user's name; ignoring it.", prefix)
            continue
        display = (os.getenv(f"{prefix}_DISPLAY_NAME") or "").strip() or username
        users[username] = (User(username=username, display_name=display, role=role), pw_hash.encode())
    if not users:
        raise AuthNotConfigured("No users configured: set ANALYST_USERNAME / ANALYST_PASSWORD_HASH "
                                "and VIEWER_USERNAME / VIEWER_PASSWORD_HASH in .env.")
    return users


def auth_configured() -> bool:
    """True if JWT_SECRET and at least one demo user are set (for /health)."""
    try:
        _secret()
        _configured_users()
        return True
    except AuthNotConfigured:
        return False


# --------------------------------------------------------------------------- #
# Passwords and tokens
# --------------------------------------------------------------------------- #

def hash_password(password: str) -> str:
    data = password.encode("utf-8")
    if len(data) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password is longer than {MAX_PASSWORD_BYTES} bytes (bcrypt's limit).")
    return bcrypt.hashpw(data, bcrypt.gensalt(rounds=12)).decode()


def _password_matches(password: str, pw_hash: bytes) -> bool:
    data = password.encode("utf-8")
    if len(data) > MAX_PASSWORD_BYTES:
        bcrypt.checkpw(b"x", _DUMMY_HASH)  # same cost as a real check
        return False
    try:
        return bcrypt.checkpw(data, pw_hash)
    except ValueError:  # malformed hash in .env
        log.error("A *_PASSWORD_HASH in .env is not a valid bcrypt hash.")
        return False


def authenticate(username: str, password: str) -> User | None:
    """The User if the username and password are right, else None."""
    users = _configured_users()
    entry = users.get(username.strip())
    if entry is None:
        bcrypt.checkpw(password.encode("utf-8")[:MAX_PASSWORD_BYTES], _DUMMY_HASH)
        return None
    user, pw_hash = entry
    return user if _password_matches(password, pw_hash) else None


def create_token(user: User, now: dt.datetime | None = None) -> tuple[str, dt.datetime]:
    """A signed token for the user, and when it expires."""
    now = now or dt.datetime.now(dt.timezone.utc)
    expires = now + dt.timedelta(minutes=_expire_minutes())
    claims = {
        "sub": user.username,
        "name": user.display_name,
        "role": user.role.value,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": expires,
    }
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM), expires


def decode_token(token: str) -> User:
    """The User a valid token belongs to. Raises jwt.InvalidTokenError otherwise.

    This is the function to replace for Entra ID (see the module docstring).
    """
    claims = jwt.decode(
        token,
        _secret(),
        algorithms=[ALGORITHM],  # pinned: never trust the token's own "alg" header
        audience=AUDIENCE,
        issuer=ISSUER,
        options={"require": ["sub", "exp", "iat", "iss", "aud"]},
    )
    entry = _configured_users().get(claims["sub"])
    if entry is None:
        raise jwt.InvalidTokenError("user no longer exists")
    return entry[0]  # role and name from .env, not from the token


# --------------------------------------------------------------------------- #
# FastAPI dependencies
# --------------------------------------------------------------------------- #

_bearer = HTTPBearer(auto_error=False, description="The access_token from POST /login")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail,
                         headers={"WWW-Authenticate": "Bearer"})


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> User:
    """The signed-in user, from the Authorization: Bearer header. 401 if missing or invalid."""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise _unauthorized("Sign in first: this endpoint needs a bearer token.")
    try:
        return decode_token(credentials.credentials)
    except AuthNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("Your session has expired. Please sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("Invalid token. Please sign in again.") from exc


def require_role(*roles: Role):
    """Dependency factory: the signed-in user, or 403 if their role isn't one of `roles`."""
    allowed = set(roles)

    def check(user: User = Depends(current_user)) -> User:
        if user.role not in allowed:
            names = " or ".join(r.label for r in roles)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"This is available to the {names} role only. "
                                       f"You are signed in as a {user.role.label}.")
        return user

    return check
