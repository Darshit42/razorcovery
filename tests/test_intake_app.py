"""HTTP tests for the intake workflow. Skipped when Postgres is unreachable."""
import pytest

from audit import db

pytestmark = pytest.mark.skipif(not db.ping(), reason="Postgres not reachable")

_CSV = (
    "name,phone,amount,type\n"
    "Ravi,9811100011,4200,payment\n"
    "Asha,9822200022,180,checkout\n"
    "bad,123,999,payment\n"
).encode()


# `client` fixture comes from tests/conftest.py (signed-in TestClient)


def test_upload_page_renders(client):
    r = client.get("/upload")
    assert r.status_code == 200
    assert "Upload a contact sheet" in r.text
    assert "consent" in r.text.lower()


def test_upload_without_attest_shows_preview_no_batch(client):
    r = client.post(
        "/upload",
        data={"merchant": "ChaiPoint", "batch_name": "x"},
        files={"file": ("c.csv", _CSV, "text/csv")},
    )
    assert r.status_code == 200
    assert "Preview" in r.text
    assert "1 rows read" in r.text or "3 rows read" in r.text
    assert "Invalid Indian" in r.text  # the bad row is reported, not dropped


def test_upload_with_attest_creates_batch_and_redirects(client):
    r = client.post(
        "/upload",
        data={"merchant": "pytest", "batch_name": "pytest attested run", "attest": "on"},
        files={"file": ("c.csv", _CSV, "text/csv")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/batch/batch_")
    bid = loc.split("/batch/")[1]
    try:
        page = client.get(loc)
        assert page.status_code == 200
        assert "pytest attested run" in page.text
        assert "Start batch" in page.text  # pending, not yet run

        prog = client.get(f"/batch/{bid}/progress").json()
        assert prog["total"] == 2 and prog["status"] == "pending"

        # exports work even before a run
        csv = client.get(f"/batch/{bid}/export.csv")
        assert csv.status_code == 200
        assert "customer_name" in csv.text and "Ravi" in csv.text
        js = client.get(f"/batch/{bid}/export.json").json()
        assert js["batch"]["id"] == bid and len(js["rows"]) == 2
    finally:
        from audit import db

        with db.get_conn() as conn:
            conn.execute("DELETE FROM batch WHERE id=%s", (bid,))


def test_unknown_batch_404(client):
    assert client.get("/batch/batch_doesnotexist").status_code == 404
    assert client.get("/batch/batch_x/progress").status_code == 404


def test_batches_list_page(client):
    r = client.get("/batches")
    assert r.status_code == 200
    assert "Batches" in r.text
