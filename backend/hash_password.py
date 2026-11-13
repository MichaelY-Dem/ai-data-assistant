"""Print a bcrypt hash for a demo user's password, to paste into .env.

The password is typed at a hidden prompt (twice) and never echoed, stored or logged.

Usage, from the project root:
    .venv\\Scripts\\python.exe -m backend.hash_password
"""
from __future__ import annotations

import getpass
import sys

from backend.auth import MAX_PASSWORD_BYTES, hash_password

MIN_LENGTH = 12


def main() -> None:
    password = getpass.getpass("Password: ")
    if len(password) < MIN_LENGTH:
        sys.exit(f"Use at least {MIN_LENGTH} characters.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        sys.exit(f"Use at most {MAX_PASSWORD_BYTES} bytes (bcrypt's limit).")
    if getpass.getpass("Repeat:   ") != password:
        sys.exit("The passwords don't match.")
    print(hash_password(password))


if __name__ == "__main__":
    main()
