import uuid

import pytest

from audit import db
from tests.conftest import TEST_EMAIL_DOMAIN

pytestmark = pytest.mark.skipif(not db.ping(), reason="Postgres not reachable")


@pytest.fixture()
def creds():
    return f"u_{uuid.uuid4().hex[:10]}@{TEST_EMAIL_DOMAIN}", "a-good-password-1"


@pytest.fixture(autouse=True)
def _cleanup(creds):
    """Delete only the specific test account this test creates — never a
    blanket wipe (that previously deleted real signed-up accounts)."""
    yield
    email, _ = creds
    with db.get_conn() as conn:
        conn.execute("DELETE FROM app_user WHERE email=%s", (email,))


def test_signup_login_session_logout(creds):
    from auth import service as a

    a.init_schema()
    email, pw = creds
    user = a.sign_up(email, pw, "Nom")
    assert user.email == email

    token = a.log_in(email, pw)
    assert a.session_user(token).email == email

    a.log_out(token)
    assert a.session_user(token) is None


def test_rejects_bad_input(creds):
    from auth import service as a

    email, pw = creds
    with pytest.raises(a.AuthError):
        a.sign_up("not-an-email", pw)
    with pytest.raises(a.AuthError):
        a.sign_up(email, "short")
    a.sign_up(email, pw)
    with pytest.raises(a.AuthError):
        a.sign_up(email, pw)              # duplicate
    with pytest.raises(a.AuthError):
        a.log_in(email, "wrong-password")


def test_session_user_none_for_garbage():
    from auth import service as a

    assert a.session_user(None) is None
    assert a.session_user("nope-not-a-real-token") is None
