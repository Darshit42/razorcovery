"""Postgres connection helpers and schema init for the audit trail."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and point it "
            "at a running Postgres instance."
        )
    return url


_CONNECT_RETRIES = 6
_CONNECT_BACKOFF_S = 1.0


def _connect():
    """psycopg.connect with a retry — RDS DNS resolution / TLS handshakes
    flake transiently on some networks and we don't want a whole batch to
    die for it."""
    last: Exception | None = None
    for attempt in range(_CONNECT_RETRIES):
        try:
            return psycopg.connect(database_url(), connect_timeout=10)
        except psycopg.OperationalError as exc:
            last = exc
            if attempt < _CONNECT_RETRIES - 1:
                time.sleep(_CONNECT_BACKOFF_S * (attempt + 1))
    raise last  # type: ignore[misc]


@contextmanager
def get_conn():
    """Yield a psycopg connection; commit on success, rollback on error."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create the audit_log table + append-only guard if not present."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_conn() as conn:
        conn.execute(sql)


def ping() -> bool:
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


if __name__ == "__main__":
    init_db()
    print("audit_log schema ready")
