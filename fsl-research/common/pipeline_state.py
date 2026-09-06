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
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

STATE_BRANCH = "pipeline-state"
STATE_FILE = "state/batches.json"
REQUESTS_DIR = "state/requests"
TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}


def record_key(record: dict) -> tuple[str, str, int]:
    """Stable identity for a record across its whole lifecycle -- promoting a
    queued record to a real batch changes its batch_id and status but never
    its experiment, source_file, or the run_number of whichever Submit run
    first produced it, so this triple is safe to use as a merge key even
    when a write_state `resolve` callback is comparing "the record I'm
    updating" against "whatever the current remote tip actually has"."""
    return (record["experiment"], record["source_file"], int(record.get("run_number", 0) or 0))


def merge_records(current_records: list[dict], *updates: list[dict]) -> list[dict]:
    """Merge record sets by stable record_key, with later values taking precedence."""
    merged: dict[tuple[str, str, int], dict] = {record_key(r): r for r in current_records}
    for record_set in updates:
        for record in record_set:
            merged[record_key(record)] = record
    return list(merged.values())


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
    resolve,
    commit_message: str,
    build_extra_files=None,
    attempts: int = 5,
) -> None:
    """Recompute and overwrite state/batches.json, committing and pushing to
    `pipeline-state`.

    Unlike a plain "pass the final records list" API, this takes a
    `resolve(current_records) -> list[dict] | None` callback. On every
    attempt -- including retries after a lease conflict -- `current_records`
    is read fresh from whatever is actually on the remote branch tip at that
    moment, and `resolve` is called again to decide what the new content
    should be. Returning `None` means "abort, nothing to write" (distinct
    from `[]`, a legitimate empty state).

    This matters because a bare retry-with-the-same-snapshot is unsafe: two
    writers (e.g. a push-triggered Submit run superseding an old run, racing
    a cron-triggered Retrieval run promoting one of that old run's queued
    records) can each read state, do real work based on what they read
    (create an OpenAI batch, cancel one, close a PR), and then both try to
    write. Whichever writes first wins the lease; the second writer's retry
    must reconcile against what the first one actually landed, not blindly
    reapply a plan made against data that's since changed underneath it.
    `resolve` is how each caller expresses "what should happen to the
    records I care about, given whatever is really there right now" --
    typically via common.pipeline_state.record_key() to match a record
    across renames (e.g. a promoted batch_id) safely.

    `build_extra_files(new_records) -> dict[str, bytes]`, if given, is also
    called fresh each attempt against the just-resolved records, so any
    request-file copies it writes correspond to the content actually being
    committed.

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
    concurrent write still loses the lease and gets retried -- now safely,
    since `resolve` re-runs against the winner's actual result instead of
    stomping it.
    """
    subprocess.run(["git", "-C", str(repo_root), "fetch", "origin", STATE_BRANCH], capture_output=True, text=True)

    for attempt in range(attempts):
        subprocess.run(["git", "-C", str(repo_root), "branch", "-D", STATE_BRANCH], capture_output=True)

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
                for entry in Path(worktree_dir).iterdir():
                    if entry.name == ".git":
                        continue
                    shutil.rmtree(entry) if entry.is_dir() else entry.unlink()

            # This is "the actual current truth" for this attempt: whatever is
            # really sitting on the branch tip we just checked out, not a
            # snapshot from whenever the caller started doing its work.
            state_path = Path(worktree_dir) / STATE_FILE
            current_records = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else []

            records = resolve(current_records)
            if records is None:
                print("write_state: resolve() aborted -- nothing to write.")
                subprocess.run(["git", "-C", str(repo_root), "worktree", "remove", "--force", worktree_dir], capture_output=True)
                return

            extra_files = build_extra_files(records) if build_extra_files else {}
            keep_request_paths = {
                request_file_path(r["experiment"], r.get("commit_sha", "unknown"), r["batch_id"], r["source_file"])
                for r in records
                if not r.get("dispatched")
            }

            requests_root = Path(worktree_dir) / REQUESTS_DIR
            if requests_root.exists():
                for path in requests_root.rglob("*"):
                    if path.is_file() and path.relative_to(worktree_dir).as_posix() not in keep_request_paths:
                        path.unlink()

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