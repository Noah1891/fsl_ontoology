#!/usr/bin/env python3
"""Single entry point for every experiment pipeline in this repo.

Usage:
  common/main.py saref-experiment detect        [...]
  common/main.py saref-experiment build-request [...]
  common/main.py saref-experiment run-request   [...]
  common/main.py saref-experiment validate      [...]
  common/main.py saref-experiment open-pr       [...]
  common/main.py ontoology build-request        [...]
  common/main.py ontoology run-request          [...]

Every flag after <context> <stage> is passed through unchanged to that
stage's script. `common/build_batch_request.py` and `common/run_batch_request.py`
are the two stages genuinely shared across experiments; the others remain
each experiment's own logic, just reachable from this one entry point.

ontoology's earlier stages (merge, convert-to-owl, oops-scan) and its
fix-pitfalls step are not wired in here yet -- run
ontoology/python_scripts/main.py directly for those until they're unified
too. ontolo-ci (the SHACL/Ontolo-CI experiment) is not wired in yet either.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMON = REPO_ROOT / "common"

STAGE_SCRIPTS = {
    ("saref-experiment", "detect"): REPO_ROOT / "saref-experiment/versioning/scripts/detect_new_releases.py",
    ("saref-experiment", "build-request"): COMMON / "build_batch_request.py",
    ("saref-experiment", "run-request"): COMMON / "run_batch_request.py",
    ("saref-experiment", "validate"): REPO_ROOT / "saref-experiment/versioning/scripts/render_and_validate.py",
    ("saref-experiment", "open-pr"): REPO_ROOT / "saref-experiment/versioning/scripts/open_pr.py",
    ("ontoology", "build-request"): COMMON / "build_batch_request.py",
    ("ontoology", "run-request"): COMMON / "run_batch_request.py",
}


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)

    context, stage, *rest = sys.argv[1:]
    script = STAGE_SCRIPTS.get((context, stage))
    if script is None:
        known = sorted(s for c, s in STAGE_SCRIPTS if c == context)
        print(f"Unknown stage '{stage}' for context '{context}'. Known stages for '{context}': {known}")
        raise SystemExit(1)

    cmd = [sys.executable, str(script)]
    if script.parent == COMMON:
        cmd.append(context)  # common/{build,run}_batch_request.py take context as their own subcommand
    cmd += rest
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
