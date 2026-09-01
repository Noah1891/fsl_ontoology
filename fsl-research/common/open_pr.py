#!/usr/bin/env python3
"""Open a review PR for one or more validated patches, branching by context.

Never runs automatically as part of validation, and never merges anything.
By default this only prints what it would do. Pass --push to create a local
branch, copy the validated patch(es) over their real target files, commit,
and push. Pass --create-pr (implies --push) to also open a GitHub PR via
`gh` -- a human still reviews and merges it.

The git/gh mechanics are identical regardless of which experiment produced
the patch, so this is one real shared implementation, not a dispatcher
around two separate ones: each context just supplies which files and what
commit/PR message to use.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.jsonio import read_json  # noqa: E402

DEFAULT_COMBINED_BRANCH = "research/combined-update"


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kwargs)


def create_pr(
    repo_root: Path,
    branch: str,
    base: str,
    patches: list[tuple[Path, str]],
    commit_message: str,
    pr_title: str,
    pr_body_path: Path | None,
    push: bool,
    open_pr_flag: bool,
    run_id: str,
) -> None:
    """patches: list of (rendered_patch_file, path_relative_to_repo_root)."""
    for patched_file, _ in patches:
        if not patched_file.exists():
            raise SystemExit(f"No validated patch at {patched_file} -- run this experiment's validation step first.")

    print(f"[{run_id}] would create branch '{branch}' from '{base}'")
    for patched_file, target_rel in patches:
        print(f"[{run_id}] would copy {patched_file} -> {repo_root / target_rel}")
    print(f"[{run_id}] would commit: {commit_message.splitlines()[0]}")
    body_note = f" using body from {pr_body_path}" if pr_body_path and pr_body_path.exists() else " using the commit message as the body"
    print(f"[{run_id}] would open PR '{pr_title}' targeting '{base}'{body_note}")

    if not push:
        print("\nDry run only -- pass --push to actually branch/commit/push, --create-pr to also open a PR.")
        return

    run(["git", "-C", str(repo_root), "checkout", "-b", branch, base])
    for patched_file, target_rel in patches:
        target = repo_root / target_rel
        shutil.copyfile(patched_file, target)
        run(["git", "-C", str(repo_root), "add", target_rel])
    run(["git", "-C", str(repo_root), "commit", "-m", commit_message])
    run(["git", "-C", str(repo_root), "push", "-u", "origin", branch])

    if open_pr_flag:
        cmd = ["gh", "pr", "create", "--title", pr_title, "--base", base, "--head", branch]
        cmd += ["--body-file", str(pr_body_path)] if pr_body_path and pr_body_path.exists() else ["--body", commit_message]
        run(cmd, cwd=str(repo_root))


def create_combined_pr(
    repo_root: Path,
    branch: str,
    base: str,
    commits: list[dict],
    pr_title: str,
    pr_body: str,
    push: bool,
    open_pr_flag: bool,
) -> None:
    """commits: [{"message": str, "patches": [(source_file, path_relative_to_repo_root), ...]}, ...].

    One commit per experiment, on one branch, in one PR -- lets a reviewer
    see and revert each experiment's contribution independently even though
    they land together. Patch files are only copied onto real repo paths
    once --push is confirmed; a dry run touches nothing outside repo_root's
    git metadata.
    """
    print("=" * 70)
    print(f"This PR will target '{base}' from new branch '{branch}':")
    print(f"  Title: {pr_title}")
    print(f"  {len(commits)} commit(s), one per experiment that produced a change:")
    for commit in commits:
        print(f"  - {commit['message'].splitlines()[0]}")
        for _, target_rel in commit["patches"]:
            print(f"      {target_rel}")
    print("=" * 70)

    if not push:
        print("\nDry run only -- pass --push to actually branch/commit/push, --create-pr to also open a PR.")
        return

    run(["git", "-C", str(repo_root), "checkout", "-b", branch, base])
    for commit in commits:
        for source, target_rel in commit["patches"]:
            shutil.copyfile(source, repo_root / target_rel)
            run(["git", "-C", str(repo_root), "add", target_rel])
        run(["git", "-C", str(repo_root), "commit", "-m", commit["message"]])
    run(["git", "-C", str(repo_root), "push", "-u", "origin", branch])

    if open_pr_flag:
        run(["gh", "pr", "create", "--title", pr_title, "--base", base, "--head", branch, "--body", pr_body],
            cwd=str(repo_root))


def _combine_open_pr(args: argparse.Namespace) -> None:
    manifest_paths = sorted(args.manifests_dir.glob("**/manifest.json")) + sorted(args.manifests_dir.glob("**/*.manifest.json"))
    if not manifest_paths:
        print(f"No manifest.json files found under {args.manifests_dir} -- nothing to commit.")
        return

    commits = []
    included = []
    for manifest_path in manifest_paths:
        manifest = read_json(manifest_path)
        experiment = manifest["experiment"]
        patches = []

        for patch in manifest.get("patches", []):
            source = manifest_path.parent / patch["source"]
            target_rel = patch["target"]
            if not source.exists():
                print(f"[{experiment}] WARNING: expected patch {source} not found, skipping this patch")
                continue
            patches.append((source, target_rel))

        if not patches:
            print(f"[{experiment}] no changes, skipping commit")
            continue

        commits.append({"message": manifest["commit_message"], "patches": patches})
        included.append(experiment)

    if not commits:
        print("No experiment produced any changes -- not opening a PR.")
        return

    pr_title = args.title or f"[NEEDS REVIEW] research: combined update from {', '.join(included)}"
    pr_body = args.body or (
        "This PR bundles automated candidate changes from the parallel research pipeline, "
        "one commit per experiment. Every commit has passed automated validation only -- "
        "none has been human-reviewed. Review each commit individually before merging.\n\n"
        + "\n".join(f"- {c['message'].splitlines()[0]}" for c in commits)
    )

    create_combined_pr(
        repo_root=args.repo_root,
        branch=args.branch,
        base=args.base,
        commits=commits,
        pr_title=pr_title,
        pr_body=pr_body,
        push=args.push,
        open_pr_flag=args.create_pr,
    )


def _saref_experiment_open_pr(args: argparse.Namespace) -> None:
    evidence = read_json(args.evidence)
    run_id = evidence["runId"]
    module_rel = evidence["targetModule"]
    patched_module = args.patch_dir / f"{run_id}-{Path(module_rel).name}"
    review_notes = args.patch_dir / f"{run_id}.md"

    branch = f"versioning/{run_id}"
    commit_message = f"Add {evidence['parentEntity']} {evidence['version']} version entry\n\nSource: {evidence['officialSource']}"
    pr_title = f"versioning: add {evidence['parentEntity']} {evidence['version']}"

    create_pr(
        repo_root=args.repo_root,
        branch=branch,
        base=args.base,
        patches=[(patched_module, module_rel)],
        commit_message=commit_message,
        pr_title=pr_title,
        pr_body_path=review_notes,
        push=args.push,
        open_pr_flag=args.create_pr,
        run_id=run_id,
    )


def _ontoology_open_pr(args: argparse.Namespace) -> None:
    # ontoology has no existing PR-opening logic to preserve -- this wires the
    # shared mechanics off explicit CLI arguments rather than guessing at
    # fix_pitfalls.py's output shape.
    create_pr(
        repo_root=args.repo_root,
        branch=args.branch,
        base=args.base,
        patches=[(args.patched_file, args.target)],
        commit_message=args.message,
        pr_title=args.title,
        pr_body_path=args.body_file,
        push=args.push,
        open_pr_flag=args.create_pr,
        run_id=args.branch,
    )


def open_pr(context: str, args: argparse.Namespace) -> None:
    if context == "saref-experiment":
        _saref_experiment_open_pr(args)
    elif context == "ontoology":
        _ontoology_open_pr(args)
    elif context == "combine":
        _combine_open_pr(args)
    else:
        raise ValueError(f"Unknown context: {context}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="context", required=True)

    saref = subparsers.add_parser("saref-experiment")
    saref.add_argument("--evidence", required=True, type=Path)
    saref.add_argument("--patch-dir", type=Path,
                        default=REPO_ROOT / "saref-experiment" / "results" / "versioning" / "candidate-patches")
    saref.add_argument("--repo-root", type=Path, default=REPO_ROOT.parent)
    saref.add_argument("--base", default="main")
    saref.add_argument("--push", action="store_true")
    saref.add_argument("--create-pr", action="store_true")

    ontoology = subparsers.add_parser("ontoology")
    ontoology.add_argument("--patched-file", required=True, type=Path, help="Rendered patch file to copy over --target")
    ontoology.add_argument("--target", required=True, help="Path relative to --repo-root that the patch replaces")
    ontoology.add_argument("--branch", required=True)
    ontoology.add_argument("--message", required=True, help="Commit message")
    ontoology.add_argument("--title", required=True, help="PR title")
    ontoology.add_argument("--body-file", type=Path, default=None)
    ontoology.add_argument("--repo-root", type=Path, default=REPO_ROOT.parent)
    ontoology.add_argument("--base", default="main")
    ontoology.add_argument("--push", action="store_true")
    ontoology.add_argument("--create-pr", action="store_true")

    combine = subparsers.add_parser("combine")
    combine.add_argument("--manifests-dir", required=True, type=Path,
                          help="Directory containing one or more downloaded experiment artifact dirs, "
                               "each with a *.manifest.json / manifest.json")
    combine.add_argument("--branch", default=DEFAULT_COMBINED_BRANCH)
    combine.add_argument("--base", default="main")
    combine.add_argument("--title", default=None)
    combine.add_argument("--body", default=None)
    combine.add_argument("--repo-root", type=Path, default=REPO_ROOT.parent)
    combine.add_argument("--push", action="store_true")
    combine.add_argument("--create-pr", action="store_true")

    args = parser.parse_args()
    if args.create_pr:
        args.push = True
    open_pr(args.context, args)


if __name__ == "__main__":
    main()
