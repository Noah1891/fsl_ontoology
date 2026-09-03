"""Git-branch-backed state for the cross-run parts of the pipeline.

Build+Submit (push-triggered) and Retrieval+Dispatch (cron-triggered) are
separate workflow runs, so this is the only thing that makes the pipeline
non-stateless: an orphan branch (`pipeline-state`) holding a small JSON file
of in-flight/completed batch records, plus a copy of each batch's original
request file (Dispatch needs the exact request body an experiment's own
post-processing script was given, e.g. ontoology's fix_pitfalls.py --requests,
and that only ever existed as a Submit-job artifact otherwise). Read with
`git show` against the remote ref (no checkout needed); written through a
throwaway `git worktree` so it never disturbs the job's primary checkout.
"""

import json
import subprocess
import tempfile
import time
from pathlib import Path

STATE_BRANCH = "pipeline-state"
STATE_FILE = "state/batches.json"
REQUESTS_DIR = "state/requests"
TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}


def _run_id_sort_key(record: dict) -> int:
    try:
        return int(record.get("submitted_run_id", 0))
    except (TypeError, ValueError):
        return -1


def latest_only(records: list[dict]) -> tuple[list[dict], list[dict]]:
    latest_run_id: dict[tuple[str, str], int] = {}
    for r in records:
        key = (r["experiment"], r["source_file"])
        run_id = _run_id_sort_key(r)
        if run_id > latest_run_id.get(key, -1):
            latest_run_id[key] = run_id

    current, superseded = [], []
    for r in records:
        key = (r["experiment"], r["source_file"])
        (current if _run_id_sort_key(r) == latest_run_id[key] else superseded).append(r)
    return current, superseded


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def _branch_exists_on_remote(repo_root: Path) -> bool:
    """Whether origin/<STATE_BRANCH> resolves locally. Every caller does a
    `git fetch origin STATE_BRANCH` immediately before checking this, so this
    reads that already-fetched remote-tracking ref instead of making a
    second live network round-trip (an `ls-remote` here previously) that can
    independently flake and disagree with the fetch -- a false "doesn't
    exist" sends write_state down the orphan-from-HEAD path onto a branch
    that already exists, which is destructive (see write_state)."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--verify", "-q", f"origin/{STATE_BRANCH}"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def read_state(repo_root: Path) -> list[dict]:
    """Every known batch record, or [] if the state branch doesn't exist yet."""
    subprocess.run(["git", "-C", str(repo_root), "fetch", "origin", STATE_BRANCH], capture_output=True, text=True)
    if not _branch_exists_on_remote(repo_root):
        return []
    result = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"origin/{STATE_BRANCH}:{STATE_FILE}"],
        capture_output=True, text=True,
    )
    return json.loads(result.stdout) if result.returncode == 0 else []


def request_file_path(experiment: str, commit_sha: str, batch_id: str, source_file: str) -> str:
    """Keyed by the commit Submit built the request against, then batch_id
    (not just source_file/experiment) so resubmitting an experiment -- from
    the same commit or a new one, before an earlier batch of the same name
    has been dispatched -- never overwrites that earlier batch's request
    body. The commit_sha segment also makes each run's files unique on
    sight, so nothing ever collides even without knowing batch_id."""
    return f"{REQUESTS_DIR}/{experiment}/{commit_sha}/{batch_id}/{source_file}"


def read_request_file(repo_root: Path, experiment: str, commit_sha: str, batch_id: str, source_file: str) -> bytes | None:
    """The verbatim request file Submit persisted for one batch, or None."""
    path = request_file_path(experiment, commit_sha, batch_id, source_file)
    result = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"origin/{STATE_BRANCH}:{path}"],
        capture_output=True,
    )
    return result.stdout if result.returncode == 0 else None


def write_state(
    repo_root: Path,
    records: list[dict],
    commit_message: str,
    extra_files: dict[str, bytes] | None = None,
    attempts: int = 5,
) -> None:
    """Overwrite state/batches.json with `records` (the full desired content --
    callers merge with read_state themselves) and add any extra_files (e.g.
    state/requests/<experiment>/<file>), committing and pushing to
    `pipeline-state`.

    Request files belonging to a record already marked `dispatched` are
    dropped from the tree: once Dispatch has consumed a batch's request body
    it's never read again (retrieve_batches.py and dispatch_experiments.py
    both filter on `dispatched`), so keeping it around forever just makes
    every future clone/fetch of this branch bigger for no reason.

    Each call replaces the branch's tip with a fresh orphan commit (no
    parent) instead of stacking a new commit on top -- nothing ever reads
    `pipeline-state` history, only its current tip (see read_state /
    read_request_file, both `git show origin/<branch>:<path>`) -- so there's
    no reason to keep old commits around either. The push uses
    --force-with-lease against the commit this call actually fetched, so a
    concurrent write (a push-triggered Submit run racing a cron
    Retrieval/Dispatch run) still loses the lease and gets retried, the same
    race this function has always had to tolerate.
    """
    extra_files = extra_files or {}
    keep_request_paths = {
        request_file_path(r["experiment"], r.get("commit_sha", "unknown"), r["batch_id"], r["source_file"])
        for r in records
        if not r.get("dispatched")
    }
    subprocess.run(["git", "-C", str(repo_root), "fetch", "origin", STATE_BRANCH], capture_output=True, text=True)

    for attempt in range(attempts):
        with tempfile.TemporaryDirectory() as worktree_dir:
            branch_exists = _branch_exists_on_remote(repo_root)
            if branch_exists:
                remote_sha = subprocess.run(
                    ["git", "-C", str(repo_root), "rev-parse", f"origin/{STATE_BRANCH}"],
                    capture_output=True, text=True, check=True,
                ).stdout.strip()
                _run(["git", "-C", str(repo_root), "worktree", "add", "--detach", worktree_dir, f"origin/{STATE_BRANCH}"])
                _run(["git", "-C", worktree_dir, "checkout", "--orphan", STATE_BRANCH])
            else:
                remote_sha = None
                _run(["git", "-C", str(repo_root), "worktree", "add", "--detach", "--no-checkout", worktree_dir, "HEAD"])
                _run(["git", "-C", worktree_dir, "checkout", "--orphan", STATE_BRANCH])
                _run(["git", "-C", worktree_dir, "rm", "-rf", "--cached", "--quiet", "."])

            requests_root = Path(worktree_dir) / REQUESTS_DIR
            if requests_root.exists():
                for path in requests_root.rglob("*"):
                    if path.is_file() and path.relative_to(worktree_dir).as_posix() not in keep_request_paths:
                        path.unlink()

            state_path = Path(worktree_dir) / STATE_FILE
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")

            for rel_path, content in extra_files.items():
                dest = Path(worktree_dir) / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)

            unexpected = [p.name for p in Path(worktree_dir).iterdir() if p.name != ".git" and p.name != "state"]
            if unexpected:
                raise RuntimeError(
                    f"Refusing to write pipeline-state: unexpected top-level entries in worktree: {unexpected}"
                )

            _run(["git", "-C", worktree_dir, "add", "-A"])
            commit = subprocess.run(
                ["git", "-C", worktree_dir, "commit", "-m", commit_message], capture_output=True, text=True,
            )
            if commit.returncode != 0:
                if "nothing to commit" in (commit.stdout + commit.stderr):
                    subprocess.run(["git", "-C", str(repo_root), "worktree", "remove", "--force", worktree_dir])
                    return
                raise RuntimeError(f"git commit failed: {commit.stderr}")

            lease = f"refs/heads/{STATE_BRANCH}:{remote_sha or ''}"
            push = subprocess.run(
                ["git", "-C", worktree_dir, "push", f"--force-with-lease={lease}", "origin", f"HEAD:{STATE_BRANCH}"],
                capture_output=True, text=True,
            )
            subprocess.run(["git", "-C", str(repo_root), "worktree", "remove", "--force", worktree_dir], capture_output=True)
            if push.returncode == 0:
                return
            subprocess.run(["git", "-C", str(repo_root), "fetch", "origin", STATE_BRANCH], capture_output=True, text=True)
            time.sleep(2 * (attempt + 1))

    raise RuntimeError(f"Failed to push {STATE_BRANCH} after {attempts} attempts")
