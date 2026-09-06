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
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import pipeline_state  # noqa: E402
from common.pipeline_state import TERMINAL_STATUSES  # noqa: E402
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
    parser.add_argument("--input-dir", required=False, type=Path, default=None,
                        help="Directory of batch-requests-*/ artifacts (omit when only processing queued requests)")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT.parent)
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument("--commit-sha", default=os.environ.get("GITHUB_SHA", "local"))
    parser.add_argument("--token-limit", type=int, default=int(os.environ.get("BATCH_TOKEN_LIMIT", 800000)))
    parser.add_argument("--process-queued", action="store_true",
                        help="Process queued request files already saved in pipeline-state if budget allows")
    args = parser.parse_args()

    request_files = []
    if args.input_dir:
        request_files = _discover_request_files(args.input_dir)
        if not request_files and not args.process_queued:
            print(f"No batch-requests-*/*.jsonl files found under {args.input_dir} -- nothing to submit.")
            return

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set.")
    from openai import OpenAI

    client = OpenAI(api_key=api_key)

    token_limit = args.token_limit

    def estimate_tokens_local(content: bytes) -> int:
        # Heuristic token estimate: 1 token ~= 4 characters/bytes. Keep at least 1.
        return max(1, len(content) // 4)

    def count_tokens_via_api(file_bytes: bytes) -> int:
        """Count tokens for every line (request) in a batch file using the
        Responses input_tokens.count endpoint. Falls back to the local
        heuristic if the API call fails or the method is unavailable.
        """
        total = 0
        try:
            for raw_line in file_bytes.splitlines():
                if not raw_line.strip():
                    continue
                try:
                    entry = json.loads(raw_line)
                except Exception:
                    # If parsing fails, fall back to heuristic for this line
                    total += estimate_tokens_local(raw_line)
                    continue
                body = entry.get("body") or {}
                model = body.get("model") or entry.get("model")
                # Prepare kwargs for the token-counting call. Include the
                # common fields the Responses API accepts.
                kwargs = {}
                for k in ("instructions", "input", "messages", "tools", "files", "images"):
                    if k in body:
                        kwargs[k] = body[k]
                if not model:
                    # If model missing, cannot use API; fallback
                    total += estimate_tokens_local(raw_line)
                    continue

                # Call the SDK counting endpoint
                if hasattr(client.responses, "input_tokens") and hasattr(client.responses.input_tokens, "count"):
                    resp = client.responses.input_tokens.count(model=model, **kwargs)
                    # SDK returns an object with input_tokens attribute
                    total += int(getattr(resp, "input_tokens", 0) or 0)
                else:
                    # No counting API available on this client
                    total += estimate_tokens_local(raw_line)
        except Exception:
            # Any failure: conservative fallback
            return estimate_tokens_local(file_bytes)
        return total

    existing = pipeline_state.read_state(args.repo_root)

    # Compute tokens currently consumed by in-flight (non-terminal, not queued) batches
    used_tokens = sum(
        r.get("tokens", 0)
        for r in existing
        if not r.get("dispatched") and r.get("status") not in TERMINAL_STATUSES and not r.get("queued")
    )

    new_records = []
    extra_files: dict[str, bytes] = {}

    # First, optionally try to process already-queued request files persisted in pipeline-state
    if args.process_queued:
        # helper to create in-memory file-like object
        import io
        def bytes_to_file(b: bytes):
            f = io.BytesIO(b)
            f.name = "request.jsonl"
            return f

        for rec in existing:
            if not rec.get("queued"):
                continue
            tokens = int(rec.get("tokens", 0))
            if used_tokens + tokens > token_limit:
                print(f"Budget full: cannot start queued {rec['source_file']} for {rec['experiment']} yet")
                continue
            print(f"Starting previously queued request {rec['source_file']} for {rec['experiment']}")
            req_bytes = pipeline_state.read_request_file(
                args.repo_root, rec["experiment"], rec.get("commit_sha", "unknown"), rec["batch_id"], rec["source_file"],
            )
            if req_bytes is None:
                print(f"Queued request file for {rec['batch_id']} missing; skipping")
                continue
            uploaded = client.files.create(file=bytes_to_file(req_bytes), purpose="batch")
            batch = create_batch(client, uploaded.id, description=f"{rec['experiment']}/{rec['source_file']}")
            print(f"[{rec['experiment']}] submitted queued {rec['source_file']} as batch {batch.id}")
            # Record new mapping (we'll add the persisted request under the new batch.id key so Dispatch can find it)
            new_path = pipeline_state.request_file_path(rec["experiment"], rec.get("commit_sha", "unknown"), batch.id, rec["source_file"])
            extra_files[new_path] = req_bytes
            # Update record in-place
            rec["batch_id"] = batch.id
            rec["status"] = batch.status
            rec["queued"] = False
            rec["submitted_run_id"] = args.run_id
            rec["tokens"] = tokens
            used_tokens += tokens

    # Now process new request files discovered in the input artifacts
    for experiment, jsonl_path in request_files:
        content = jsonl_path.read_bytes()
        tokens = count_tokens_via_api(content)
        if used_tokens + tokens > token_limit:
            # Persist as queued (store request file under a queued pseudo-batch id)
            queued_id = f"queued-{args.run_id}-{jsonl_path.name}"
            print(f"Token budget exceeded; queuing {jsonl_path.name} for {experiment} (tokens={tokens})")
            queued_record = {
                "batch_id": queued_id,
                "experiment": experiment,
                "source_file": jsonl_path.name,
                "status": "queued",
                "dispatched": False,
                "submitted_run_id": args.run_id,
                "commit_sha": args.commit_sha,
                "queued": True,
                "tokens": tokens,
            }
            new_records.append(queued_record)
            extra_files[pipeline_state.request_file_path(experiment, args.commit_sha, queued_id, jsonl_path.name)] = content
            continue

        # Submit immediately
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
            "tokens": tokens,
        })
        extra_files[
            pipeline_state.request_file_path(experiment, args.commit_sha, batch.id, jsonl_path.name)
        ] = content
        used_tokens += tokens

    pipeline_state.write_state(
        args.repo_root,
        existing + new_records,
        commit_message=f"Submit {len(new_records)} batch(es) from run {args.run_id}",
        extra_files=extra_files,
    )
    print(f"Recorded {len(new_records)} new batch(es) in {pipeline_state.STATE_BRANCH}.")


if __name__ == "__main__":
    main()
