#!/usr/bin/env python3
"""Shared batch-run entry point, branching by which experiment calls it.

SAREF: submit one small batch (built by common/build_batch_request.py),
poll it to completion, and write the structured JSON response per custom_id.
OnToology: submit every file already written under llm_prompting/batches/,
poll all of them, and write each batch's raw output file -- calls its own
existing run_batch_request.py module unmodified.
"""

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.jsonio import read_json  # noqa: E402
from common.openai_batch import extract_structured_outputs, poll_batch, submit_batch  # noqa: E402


def _read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _load_mock_responses(path: Path) -> dict:
    data = read_json(path)
    if "runId" in data:
        return {data["runId"]: data}
    return data


def _run_saref_experiment_batch(args: argparse.Namespace) -> None:
    requests = _read_jsonl(args.batch)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.mock_response:
        responses = _load_mock_responses(args.mock_response)
        for request in requests:
            custom_id = request["custom_id"]
            if custom_id not in responses:
                raise SystemExit(f"No mock response provided for custom_id '{custom_id}'")
            out_path = args.out_dir / f"{custom_id}.json"
            out_path.write_text(json.dumps(responses[custom_id], indent=2) + "\n", encoding="utf-8")
            print(f"[mock] wrote {out_path}")
        return

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit(
            "OPENAI_API_KEY is not set. Pass --mock-response to exercise this "
            "pipeline without calling OpenAI."
        )
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    batch = submit_batch(client, requests)
    print(f"Submitted batch {batch.id} ({len(requests)} request(s)), waiting for completion...")
    batch = poll_batch(client, batch.id)
    if batch.status != "completed":
        raise SystemExit(f"Batch {batch.id} ended with status '{batch.status}', not 'completed'.")

    output_text = client.files.content(batch.output_file_id).text
    responses = extract_structured_outputs(output_text)
    for custom_id, structured in responses.items():
        out_path = args.out_dir / f"{custom_id}.json"
        out_path.write_text(json.dumps(structured, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out_path}")


def _run_ontoology_batch(args: argparse.Namespace) -> None:
    ontoology_scripts = REPO_ROOT / "ontoology" / "python_scripts"
    if str(ontoology_scripts) not in sys.path:
        sys.path.insert(0, str(ontoology_scripts))
    import run_batch_request as ontoology_runner  # teammate's own module, unmodified

    batch_input_files = ontoology_runner.upload_batch_files(str(args.batches_dir))
    batches = ontoology_runner.create_batch_jobs(batch_input_files)
    response_files: dict = {}
    finished: set = set()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    try:
        responses = ontoology_runner.retrieve_batches(batches, response_files, finished)
        ontoology_runner.write_output_files(responses, str(args.out_dir))
    except ontoology_runner.BatchesNotFinished:
        print("Could not retrieve batches after trying for one hour.")
    finally:
        batch_ids = [batch.id for batch in batches]
        (args.out_dir / "batch_ids.json").write_text(json.dumps(batch_ids, indent=4), encoding="utf-8")


def run_batch_request(context: str, args: argparse.Namespace) -> None:
    if context == "saref-experiment":
        _run_saref_experiment_batch(args)
    elif context == "ontoology":
        _run_ontoology_batch(args)
    else:
        raise ValueError(f"Unknown context: {context}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="context", required=True)

    saref = subparsers.add_parser("saref-experiment")
    saref.add_argument("--batch", required=True, type=Path, help="A *.jsonl file from common/build_batch_request.py")
    saref.add_argument("--out-dir", required=True, type=Path)
    saref.add_argument("--mock-response", type=Path, help="Skip OpenAI entirely; use this JSON file as the response")

    ontoology = subparsers.add_parser("ontoology")
    ontoology.add_argument("--batches-dir", required=True, type=Path)
    ontoology.add_argument("--out-dir", required=True, type=Path)

    args = parser.parse_args()
    run_batch_request(args.context, args)


if __name__ == "__main__":
    main()
