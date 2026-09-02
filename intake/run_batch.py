"""CLI entrypoint to execute one batch.

Run as a subprocess by the web app (LiveKit plugins must register on the
main thread, so the batch cannot run in a worker thread inside uvicorn):

    python -m intake.run_batch <batch_id> [--now 2026-09-03T12:00:00+05:30]
"""
from __future__ import annotations

import argparse
from datetime import datetime

from dotenv import load_dotenv

from intake.runner import run_batch_blocking

load_dotenv()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("batch_id")
    ap.add_argument("--now", default=None)
    args = ap.parse_args()
    now = datetime.fromisoformat(args.now) if args.now else None
    progress = run_batch_blocking(args.batch_id, now=now)
    print(f"batch {args.batch_id} done: {progress}")


if __name__ == "__main__":
    main()
