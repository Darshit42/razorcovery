"""Accounts + sessions. Passwords hashed with bcrypt; sessions are
opaque random tokens stored in Postgres (no JWT, easy revocation)."""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt

from audit import db

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
SESSION_TTL = timedelta(days=14)
SESSION_COOKIE = "razorcovery_session"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    pass


@dataclass
class User:
    id: int
    email: str
    name: str


def init_schema() -> None:
    with db.get_conn() as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


def user_count() -> int:
    with db.get_conn() as conn:
        return int(conn.execute("SELECT count(*) FROM app_user").fetchone()[0])


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def _check(pw: str, pw_hash: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), pw_hash.encode())
    except ValueError:
        return False


def sign_up(email: str, password: str, name: str = "") -> User:
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address.")
    if len(password or "") < 8:
        raise AuthError("Password must be at least 8 characters.")
    init_schema()
    with db.get_conn() as conn:
        exists = conn.execute("SELECT 1 FROM app_user WHERE email=%s", (email,)).fetchone()
        if exists:
            raise AuthError("An account with that email already exists.")
        row = conn.execute(
            "INSERT INTO app_user (email, name, pw_hash) VALUES (%s,%s,%s) RETURNING id",
            (email, (name or "").strip(), _hash(password)),
        ).fetchone()
    return User(id=int(row[0]), email=email, name=(name or "").strip())


def log_in(email: str, password: str) -> str:
    email = (email or "").strip().lower()
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, pw_hash FROM app_user WHERE email=%s", (email,)
        ).fetchone()
        if not row or not _check(password, row[2]):
            raise AuthError("Wrong email or password.")
        uid = int(row[0])
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO app_session (token, user_id, expires_at) VALUES (%s,%s,%s)",
            (token, uid, datetime.now(timezone.utc) + SESSION_TTL),
        )
        conn.execute("UPDATE app_user SET last_login_at=now() WHERE id=%s", (uid,))
    return token


def session_user(token: str | None) -> User | None:
    if not token:
        return None
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT u.id, u.email, u.name FROM app_session s
               JOIN app_user u ON u.id = s.user_id
               WHERE s.token=%s AND s.expires_at > now()""",
            (token,),
        ).fetchone()
    if not row:
        return None
    return User(id=int(row[0]), email=row[1], name=row[2])


def log_out(token: str | None) -> None:
    if not token:
        return
    with db.get_conn() as conn:
        conn.execute("DELETE FROM app_session WHERE token=%s", (token,))
