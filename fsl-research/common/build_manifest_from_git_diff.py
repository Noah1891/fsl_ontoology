#!/usr/bin/env python3
"""Build a combine-ready manifest.json from git's working-tree diff.

For an experiment that writes its changes directly onto real repo files
(e.g. ontoology's fix_pitfalls.py) rather than to a separate rendered patch
file (the way saref-experiment's render_and_validate.py does), this copies
each changed file out to --out-dir and records it as a manifest 'patch' so
common/open_pr.py combine can apply it uniformly alongside experiments that
already produce their own manifest.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.jsonio import write_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--message", required=True, help="Commit message for the combined PR")
    parser.add_argument("--paths", nargs="+", required=True, help="git status/diff scope, e.g. ontologies")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT.parent)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    result = subprocess.run(
        ["git", "-C", str(args.repo_root), "status", "--porcelain", "--", *args.paths],
        check=True, capture_output=True, text=True,
    )
    changed = [line[3:].strip() for line in result.stdout.splitlines() if line.strip()]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    patches = []
    for rel in changed:
        dest = args.out_dir / Path(rel).name
        shutil.copyfile(args.repo_root / rel, dest)
        patches.append({"source": Path(rel).name, "target": rel})

    write_json(args.out_dir / "manifest.json", {
        "experiment": args.experiment,
        "commit_message": args.message,
        "patches": patches,
    })
    if patches:
        print(f"Wrote manifest for {args.experiment} covering {len(patches)} file(s) to {args.out_dir}")
    else:
        print(f"No changed files under {args.paths} -- wrote an empty manifest for {args.experiment}.")


if __name__ == "__main__":
    main()
