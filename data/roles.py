"""Who may read which portfolio tables. The one place the role rules are defined.

Used by:
  - data/create_viewer_user.py  (MySQL grants for the Viewer account)
  - mcp_server/server.py        (which account to connect as, which tables to refuse)
  - backend/                    (which role to start the MCP server with)

Two roles:
  analyst  everything in the portfolio database (account ASSISTANT_DB_USER)
  viewer   everything except deals            (account ASSISTANT_VIEWER_DB_USER)

The Viewer account is deny-by-default: it gets SELECT only on the tables in
VIEWER_TABLES, so a table added to the schema later stays hidden from Viewers
until it is added here and data/create_viewer_user.py is rerun.
"""
from __future__ import annotations

ANALYST = "analyst"
VIEWER = "viewer"
ROLES = (ANALYST, VIEWER)

# Tables the Viewer account is granted SELECT on (and nothing else).
VIEWER_TABLES = ("companies", "financials", "quarterly_financials", "assets")

# Tables each role must not see. Used for clear error messages; the MySQL grants
# are what actually enforce it.
RESTRICTED_TABLES: dict[str, frozenset[str]] = {
    ANALYST: frozenset(),
    VIEWER: frozenset({"deals"}),
}

# .env settings holding each role's MySQL account (user, password).
DB_ACCOUNT_SETTINGS: dict[str, tuple[str, str]] = {
    ANALYST: ("ASSISTANT_DB_USER", "ASSISTANT_DB_PASSWORD"),
    VIEWER: ("ASSISTANT_VIEWER_DB_USER", "ASSISTANT_VIEWER_DB_PASSWORD"),
}


def check_role(role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"Unknown role {role!r}; expected one of {', '.join(ROLES)}.")
    return role
