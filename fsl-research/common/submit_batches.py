#!/usr/bin/env python3
"""Submit phase: upload every request file, create one batch job per file.

Fully generic -- doesn't know or care which experiment produced a file, or
what's inside it. Input is a directory of `batch-requests-<experiment>/*.jsonl`
subfolders, exactly what
  actions/download-artifact: {pattern: "batch-requests-*", merge-multiple: false}
produces from the parallel Build jobs. For each file: upload it, create a
batch, and record a state entry -- no polling here, that's Retrieval's job
(a separate, later, cron-triggered workflow run).

State (which batches are in flight, and a copy of each request file so
Dispatch can hand it back to that experiment's own post-processing later)
is persisted to the `pipeline-state` git branch via common/pipeline_state,
since this run and the eventual Retrieval/Dispatch run share nothing else.
"""

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import pipeline_state  # noqa: E402
from common.openai_batch import create_batch, upload_batch_file  # noqa: E402

ARTIFACT_PREFIX = "batch-requests-"


def _discover_request_files(input_dir: Path) -> list[tuple[str, Path]]:
    """[(experiment, jsonl_path), ...] for every batch-requests-<experiment>/*.jsonl found."""
    found = []
    for experiment_dir in sorted(input_dir.iterdir()):
        if not experiment_dir.is_dir() or not experiment_dir.name.startswith(ARTIFACT_PREFIX):
            continue
        experiment = experiment_dir.name[len(ARTIFACT_PREFIX):]
        for jsonl_path in sorted(experiment_dir.glob("*.jsonl")):
            found.append((experiment, jsonl_path))
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT.parent)
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument("--commit-sha", default=os.environ.get("GITHUB_SHA", "local"))
    args = parser.parse_args()

    request_files = _discover_request_files(args.input_dir)
    if not request_files:
        print(f"No batch-requests-*/*.jsonl files found under {args.input_dir} -- nothing to submit.")
        return

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set.")
    from openai import OpenAI

    client = OpenAI(api_key=api_key)

    new_records = []
    extra_files: dict[str, bytes] = {}
    for experiment, jsonl_path in request_files:
        uploaded = upload_batch_file(client, jsonl_path)
        batch = create_batch(client, uploaded.id, description=f"{experiment}/{jsonl_path.name}")
        print(f"[{experiment}] submitted {jsonl_path.name} as batch {batch.id}")
        new_records.append({
            "batch_id": batch.id,
            "experiment": experiment,
            "source_file": jsonl_path.name,
            "status": batch.status,
            "dispatched": False,
            "submitted_run_id": args.run_id,
            "commit_sha": args.commit_sha,
        })
        extra_files[
            pipeline_state.request_file_path(experiment, args.commit_sha, batch.id, jsonl_path.name)
        ] = jsonl_path.read_bytes()

    existing = pipeline_state.read_state(args.repo_root)
    pipeline_state.write_state(
        args.repo_root,
        existing + new_records,
        commit_message=f"Submit {len(new_records)} batch(es) from run {args.run_id}",
        extra_files=extra_files,
    )
    print(f"Recorded {len(new_records)} new batch(es) in {pipeline_state.STATE_BRANCH}.")


if __name__ == "__main__":
    main()
