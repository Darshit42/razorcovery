"""Reset the recovery data to empty.

    python -m metrics.seed --wipe

There is no synthetic seeding: the dashboard, call logs and batches are
populated only by real use (uploads + real calls). `data/generate_events.py`
still exists but is used by the test suite, not the running app.
"""
from __future__ import annotations

import argparse

from dotenv import load_dotenv

from audit import db

load_dotenv()

_TABLES = ["audit_log", "batch_row", "batch"]


def wipe() -> None:
    db.init_db()
    with db.get_conn() as conn:
        conn.execute("TRUNCATE audit_log, batch_row, batch RESTART IDENTITY CASCADE")
    print("wiped: audit_log, batch_row, batch — dashboard is now empty.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wipe", action="store_true", required=True,
                    help="TRUNCATE audit_log + batch + batch_row")
    ap.parse_args()
    wipe()


if __name__ == "__main__":
    main()
