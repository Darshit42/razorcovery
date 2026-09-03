"""Shared test fixtures.

The suite shares one Postgres (no separate test DB). Tests that write
(intake batches, auth accounts, direct audit rows) leave rows behind
during the run; this conftest TRUNCATEs everything once the session ends
so `pytest` doesn't leave demo data in the app.
"""
import uuid

import pytest


@pytest.fixture(scope="session", autouse=True)
def _clean_db_after_session():
    yield
    try:
        from audit import db

        if db.ping():
            with db.get_conn() as conn:
                conn.execute(
                    "TRUNCATE audit_log, batch_row, batch, app_session, app_user "
                    "RESTART IDENTITY CASCADE"
                )
    except Exception:
        pass


@pytest.fixture()
def client():
    """A TestClient already signed in as a throwaway account."""
    from fastapi.testclient import TestClient

    from auth import service as auth_service
    from metrics.app import app

    auth_service.init_schema()
    email = f"test_{uuid.uuid4().hex[:10]}@example.com"
    auth_service.sign_up(email, "test-password-123", "Tester")
    token = auth_service.log_in(email, "test-password-123")

    c = TestClient(app)
    c.cookies.set(auth_service.SESSION_COOKIE, token)
    return c
