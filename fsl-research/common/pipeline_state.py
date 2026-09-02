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


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def _branch_exists_on_remote(repo_root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-remote", "--exit-code", "--heads", "origin", STATE_BRANCH],
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


def request_file_path(experiment: str, batch_id: str, source_file: str) -> str:
    """Keyed by batch_id (not just source_file/experiment) so resubmitting an
    experiment before an earlier batch of the same name has been dispatched
    never overwrites that earlier batch's request body."""
    return f"{REQUESTS_DIR}/{experiment}/{batch_id}/{source_file}"


def read_request_file(repo_root: Path, experiment: str, batch_id: str, source_file: str) -> bytes | None:
    """The verbatim request file Submit persisted for one batch, or None."""
    path = request_file_path(experiment, batch_id, source_file)
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
    `pipeline-state`. Retries on push rejection to tolerate a rare race
    between a push-triggered Submit run and a cron Retrieval/Dispatch run.
    """
    extra_files = extra_files or {}
    subprocess.run(["git", "-C", str(repo_root), "fetch", "origin", STATE_BRANCH], capture_output=True, text=True)

    for attempt in range(attempts):
        with tempfile.TemporaryDirectory() as worktree_dir:
            if _branch_exists_on_remote(repo_root):
                _run(["git", "-C", str(repo_root), "worktree", "add", "--detach", worktree_dir, f"origin/{STATE_BRANCH}"])
                _run(["git", "-C", worktree_dir, "checkout", "-B", STATE_BRANCH])
            else:
                _run(["git", "-C", str(repo_root), "worktree", "add", "--detach", "--no-checkout", worktree_dir, "HEAD"])
                _run(["git", "-C", worktree_dir, "checkout", "--orphan", STATE_BRANCH])
                subprocess.run(["git", "-C", worktree_dir, "rm", "-rf", "--cached", "--quiet", "."], capture_output=True)

            state_path = Path(worktree_dir) / STATE_FILE
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")

            for rel_path, content in extra_files.items():
                dest = Path(worktree_dir) / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)

            _run(["git", "-C", worktree_dir, "add", "-A"])
            commit = subprocess.run(
                ["git", "-C", worktree_dir, "commit", "-m", commit_message], capture_output=True, text=True,
            )
            if commit.returncode != 0:
                if "nothing to commit" in (commit.stdout + commit.stderr):
                    subprocess.run(["git", "-C", str(repo_root), "worktree", "remove", "--force", worktree_dir])
                    return
                raise RuntimeError(f"git commit failed: {commit.stderr}")

            push = subprocess.run(
                ["git", "-C", worktree_dir, "push", "origin", f"HEAD:{STATE_BRANCH}"], capture_output=True, text=True,
            )
            subprocess.run(["git", "-C", str(repo_root), "worktree", "remove", "--force", worktree_dir], capture_output=True)
            if push.returncode == 0:
                return
            subprocess.run(["git", "-C", str(repo_root), "fetch", "origin", STATE_BRANCH], capture_output=True, text=True)
            time.sleep(2 * (attempt + 1))

    raise RuntimeError(f"Failed to push {STATE_BRANCH} after {attempts} attempts")
