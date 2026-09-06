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
import subprocess
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
    parser.add_argument("--dry-run", action="store_true",
                         help="Report what would happen -- which batches would be polled, which "
                              "outputs would be written -- without calling OpenAI, writing any "
                              "output file, or touching pipeline-state. Doesn't need OPENAI_API_KEY.")
    args = parser.parse_args()

    records = pipeline_state.read_state(args.repo_root)
    pending = [r for r in records if not r.get("dispatched")]
    if not pending:
        print("No pending batches -- nothing to retrieve.")
        return

    current, superseded = pipeline_state.latest_only(pending)

    changed = False
    for record in superseded:
        record["dispatched"] = True
        changed = True
        verb = "would be dropped" if args.dry_run else "skipping, never dispatched"
        print(
            f"[{record['experiment']}] {record['source_file']} (batch {record['batch_id']}, "
            f"run {record['submitted_run_id']}) superseded by a later run -- {verb}"
        )

    if not current:
        if changed:
            if args.dry_run:
                print("\nDry run -- would commit 'Drop superseded batches' to pipeline-state.")
            else:
                pipeline_state.write_state(args.repo_root, records, commit_message="Drop superseded batches")
        else:
            print("No pending batches -- nothing to retrieve.")
        return

    client = None
    if not args.dry_run:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise SystemExit("OPENAI_API_KEY is not set.")
        from openai import OpenAI

        client = OpenAI(api_key=api_key)

    for record in current:
        if record.get("queued"):
            # Not actually submitted to OpenAI yet -- its "batch_id" is a
            # synthetic placeholder (see submit_batches.py's queued_id),
            # not a real batch, so there's nothing to poll. It becomes
            # pollable once a later submit_batches.py --process-queued run
            # (triggered below, or by the next Submit) gives it a real one.
            if args.dry_run:
                print(f"[{record['experiment']}] {record['source_file']}: queued, not yet submitted -- nothing to poll")
            continue

        batch = None
        if record["status"] not in TERMINAL_STATUSES:
            if args.dry_run:
                print(f"[{record['experiment']}] {record['batch_id']}: would poll (currently '{record['status']}')")
            else:
                batch = retrieve_batch(client, record["batch_id"])
                if batch.status != record["status"]:
                    print(f"[{record['experiment']}] {record['batch_id']}: {record['status']} -> {batch.status}")
                    record["status"] = batch.status
                    changed = True

        if record["status"] != "completed":
            continue

        out_path = args.outputs_dir / record["experiment"] / record["source_file"]
        if out_path.exists():
            continue  # already written on a previous tick, dispatch just hasn't consumed it yet

        if args.dry_run:
            print(f"[{record['experiment']}] {record['batch_id']}: completed -- would fetch output to {out_path}")
            continue

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
        if args.dry_run:
            print("\nDry run -- would commit 'Update batch statuses' to pipeline-state and attempt queued requests.")
        else:
            pipeline_state.write_state(args.repo_root, records, commit_message="Update batch statuses")
            # After freeing tokens by updating completed batches, attempt to
            # start any previously-queued request files so work can proceed
            # without waiting for a new push-triggered Submit run.
            print("Attempting to start any queued requests now that statuses changed...")
            subprocess.run([
                sys.executable, str(REPO_ROOT / "common" / "submit_batches.py"),
                "--process-queued", "--repo-root", str(args.repo_root),
                "--run-id", os.environ.get("GITHUB_RUN_ID", "retrieval"),
                "--commit-sha", os.environ.get("GITHUB_SHA", "retrieval"),
            ], check=False)
    else:
        print("No status changes this tick.")


if __name__ == "__main__":
    main()
