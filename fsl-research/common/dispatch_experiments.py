#!/usr/bin/env python3
"""Dispatch phase: once an experiment's batches are all done, post-process
and land its change on the shared combined PR.

Content-aware, unlike Submit/Retrieval -- each experiment gets its own
post-processing here, run exactly the way it used to run inline in that
experiment's workflow job, just relocated to a separate, later, cron-
triggered run once its batch(es) have actually finished:
  - saref-experiment: parse the raw batch output, then
    versioning/scripts/render_and_validate.py (unchanged; already emits a
    <run_id>.manifest.json).
  - ontoology: fix_pitfalls.py per completed pitfall batch (unchanged),
    then common/build_manifest_from_git_diff.py (unchanged).
  - ontolo-ci: not implemented yet, matches its Build-phase placeholder.

An experiment is "ready" once every one of its un-dispatched batch records
has a terminal status and at least one succeeded. Once ready experiments
produce their manifests, one common/open_pr.py combine call lands everything
onto the shared, incrementally-updated combined-update branch/PR -- state is
only marked dispatched after that push succeeds, so a failure just leaves
the batch(es) pending for the next cron tick to retry.
"""

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import pipeline_state  # noqa: E402
from common.jsonio import write_json  # noqa: E402
from common.openai_batch import extract_structured_outputs  # noqa: E402
from common.pipeline_state import TERMINAL_STATUSES  # noqa: E402


def _dispatch_saref_experiment(repo_root: Path, completed: list[dict], outputs_dir: Path, manifest_dir: Path) -> bool:
    saref_dir = REPO_ROOT / "saref-experiment"
    scripts_dir = saref_dir / "versioning" / "scripts"
    out_patch_dir = manifest_dir / "saref-experiment"
    parsed_dir = manifest_dir / "_scratch" / "saref-experiment-responses"
    parsed_dir.mkdir(parents=True, exist_ok=True)

    produced = False
    for record in completed:
        stem = Path(record["source_file"]).stem
        evidence_path = saref_dir / "versioning" / "src" / f"{stem}.json"
        raw_path = outputs_dir / record["experiment"] / record["source_file"]
        if not evidence_path.exists() or not raw_path.exists():
            print(f"[saref-experiment] skipping {record['source_file']}: missing evidence or retrieved output")
            continue

        parsed = extract_structured_outputs(raw_path.read_text(encoding="utf-8"))
        for custom_id, structured in parsed.items():
            response_path = parsed_dir / f"{custom_id}.json"
            write_json(response_path, structured)
            result = subprocess.run([
                sys.executable, str(scripts_dir / "render_and_validate.py"),
                "--evidence", str(evidence_path),
                "--response", str(response_path),
                "--out-patch-dir", str(out_patch_dir),
            ])
            if result.returncode == 0:
                produced = True
            else:
                print(f"[saref-experiment] render_and_validate.py failed for {custom_id}, continuing")
    return produced


def _dispatch_ontoology(repo_root: Path, completed: list[dict], outputs_dir: Path, manifest_dir: Path) -> bool:
    ontoology_scripts = REPO_ROOT / "ontoology" / "python_scripts"
    requests_scratch = manifest_dir / "_scratch" / "ontoology-requests"
    requests_scratch.mkdir(parents=True, exist_ok=True)

    applied_any = False
    for record in completed:
        pitfall_id = record["source_file"].removeprefix("batch_input_").removesuffix(".jsonl")
        raw_path = outputs_dir / record["experiment"] / record["source_file"]
        if not raw_path.exists():
            print(f"[ontoology] skipping pitfall {pitfall_id}: no retrieved output")
            continue

        request_bytes = pipeline_state.read_request_file(repo_root, record["experiment"], record["source_file"])
        if request_bytes is None:
            print(f"[ontoology] no persisted request file for {record['source_file']}, skipping")
            continue
        request_path = requests_scratch / record["source_file"]
        request_path.write_bytes(request_bytes)

        print(f"Applying fixes for pitfall {pitfall_id}")
        result = subprocess.run([
            sys.executable, str(ontoology_scripts / "fix_pitfalls.py"),
            "--pitfall", pitfall_id,
            "--requests", str(request_path),
            "--results", str(raw_path),
        ])
        if result.returncode != 0:
            print(f"fix_pitfalls.py failed for pitfall {pitfall_id}, continuing")
        applied_any = True

    if not applied_any:
        return False

    result = subprocess.run([
        sys.executable, str(REPO_ROOT / "common" / "build_manifest_from_git_diff.py"),
        "--experiment", "ontoology",
        "--message", "Apply OOPS! pitfall fixes",
        "--paths", "ontologies",
        "--repo-root", str(repo_root),
        "--out-dir", str(manifest_dir / "ontoology"),
    ])
    return result.returncode == 0


DISPATCHERS = {
    "saref-experiment": _dispatch_saref_experiment,
    "ontoology": _dispatch_ontoology,
    # "ontolo-ci": not implemented yet -- its Build job produces nothing to dispatch.
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT.parent)
    parser.add_argument("--outputs-dir", required=True, type=Path)
    parser.add_argument("--manifests-dir", required=True, type=Path)
    parser.add_argument("--branch", default="research/combined-update")
    parser.add_argument("--base", default="main")
    parser.add_argument("--dry-run", action="store_true", help="Run dispatchers but skip opening/updating the PR and updating state")
    args = parser.parse_args()

    records = pipeline_state.read_state(args.repo_root)
    by_experiment: dict[str, list[dict]] = {}
    for record in records:
        if record.get("dispatched"):
            continue
        by_experiment.setdefault(record["experiment"], []).append(record)

    ready = []
    for experiment, exp_records in by_experiment.items():
        if not all(r["status"] in TERMINAL_STATUSES for r in exp_records):
            print(f"[{experiment}] still has batch(es) in flight, not ready")
            continue
        completed = [r for r in exp_records if r["status"] == "completed"]
        if not completed:
            print(f"[{experiment}] every batch ended without success, nothing to dispatch")
            continue
        dispatcher = DISPATCHERS.get(experiment)
        if dispatcher is None:
            print(f"[{experiment}] no dispatcher registered yet, leaving pending")
            continue
        ready.append((experiment, exp_records, completed, dispatcher))

    if not ready:
        print("No experiment is fully ready to dispatch yet.")
        return

    dispatched_records: list[dict] = []
    for experiment, exp_records, completed, dispatcher in ready:
        print(f"=== Dispatching {experiment} ===")
        if dispatcher(args.repo_root, completed, args.outputs_dir, args.manifests_dir):
            dispatched_records.extend(exp_records)
        else:
            print(f"[{experiment}] produced no manifest, will retry next tick")

    if not dispatched_records:
        print("No experiment produced a manifest this tick.")
        return

    if args.dry_run:
        print(f"Dry run -- would combine {len(dispatched_records)} batch record(s) into one PR; skipping.")
        return

    combine = subprocess.run([
        sys.executable, str(REPO_ROOT / "common" / "open_pr.py"), "combine",
        "--manifests-dir", str(args.manifests_dir),
        "--repo-root", str(args.repo_root),
        "--branch", args.branch,
        "--base", args.base,
        "--push", "--create-pr",
    ])
    if combine.returncode != 0:
        raise SystemExit("open_pr.py combine failed -- leaving state undispatched for retry next tick.")

    for record in dispatched_records:
        record["dispatched"] = True
    pipeline_state.write_state(
        args.repo_root, records, commit_message=f"Mark {len(dispatched_records)} batch(es) dispatched",
    )
    print(f"Dispatched {len(dispatched_records)} batch record(s) across {len(ready)} experiment(s).")


if __name__ == "__main__":
    main()
