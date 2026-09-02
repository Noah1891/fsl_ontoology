#!/usr/bin/env python3
"""Retrieval phase: one poll pass over every pending batch, per cron tick.

Fully generic, like Submit -- reads the shared state from the
`pipeline-state` branch, checks each non-terminal batch's status once, and
writes the raw output text of any batch that's completed (and not yet
dispatched) immediately, rather than waiting for every batch to finish. That
incremental write is the fix for the old behaviour (see
ontoology/python_scripts/run_batch_request.py's retrieve_batches, which only
wrote output after every batch in the run had reached a terminal state):
here each cron tick only needs to make forward progress, not finish
everything in one shot -- the schedule itself is the retry loop.

Dispatch (the next step, same job) reads these files back from --outputs-dir
and decides what's ready to post-process.
"""

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import pipeline_state  # noqa: E402
from common.openai_batch import fetch_batch_output_text, retrieve_batch  # noqa: E402
from common.pipeline_state import TERMINAL_STATUSES  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT.parent)
    parser.add_argument("--outputs-dir", required=True, type=Path)
    args = parser.parse_args()

    records = pipeline_state.read_state(args.repo_root)
    pending = [r for r in records if not r.get("dispatched")]
    if not pending:
        print("No pending batches -- nothing to retrieve.")
        return

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set.")
    from openai import OpenAI

    client = OpenAI(api_key=api_key)

    changed = False
    for record in pending:
        if record["status"] not in TERMINAL_STATUSES:
            batch = retrieve_batch(client, record["batch_id"])
            if batch.status != record["status"]:
                print(f"[{record['experiment']}] {record['batch_id']}: {record['status']} -> {batch.status}")
                record["status"] = batch.status
                changed = True
        else:
            batch = None

        if record["status"] != "completed":
            continue

        out_path = args.outputs_dir / record["experiment"] / record["source_file"]
        if out_path.exists():
            continue  # already written on a previous tick, dispatch just hasn't consumed it yet

        if batch is None:
            batch = retrieve_batch(client, record["batch_id"])
        output_text = fetch_batch_output_text(client, batch)
        if output_text is None:
            print(f"[{record['experiment']}] {record['batch_id']}: completed with no output or error file")
            continue

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output_text, encoding="utf-8")
        print(f"[{record['experiment']}] wrote {out_path}")

    if changed:
        pipeline_state.write_state(args.repo_root, records, commit_message="Update batch statuses")
    else:
        print("No status changes this tick.")


if __name__ == "__main__":
    main()
