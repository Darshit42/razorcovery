"""Shared test fixtures.

The suite shares one Postgres (no separate test DB) — there used to be a
blanket TRUNCATE of app_user/app_session/audit_log/batch* after the test
session. That was a real bug: it deleted real signed-up accounts (and
would delete real batches/calls) any time the suite ran, not just test
data. Removed. Cleanup is now precise:
  - accounts created by tests use the @pytest.razorcovery.invalid domain
    and are deleted by id in fixture teardown (app_user isn't append-only)
  - batches created by tests are deleted by id in teardown (batch/batch_row
    aren't append-only either)
  - audit_log IS append-only by design (PRD compliance requirement) — test
    rows there are prefixed pytest_evt_/testcall_ and are simply left, the
    same way a real call's audit rows are never deleted.
"""
import uuid

import pytest

TEST_EMAIL_DOMAIN = "pytest.razorcovery.invalid"


def unique_test_email() -> str:
    return f"t_{uuid.uuid4().hex[:12]}@{TEST_EMAIL_DOMAIN}"


@pytest.fixture()
def client():
    """A TestClient signed in as a throwaway account, deleted on teardown."""
    from fastapi.testclient import TestClient

    from audit import db
    from auth import service as auth_service
    from metrics.app import app

    auth_service.init_schema()
    email = unique_test_email()
    user = auth_service.sign_up(email, "test-password-123", "Tester")
    token = auth_service.log_in(email, "test-password-123")

    c = TestClient(app)
    c.cookies.set(auth_service.SESSION_COOKIE, token)
    yield c

    with db.get_conn() as conn:
        conn.execute("DELETE FROM app_user WHERE id=%s", (user.id,))
