"""Shared commit-message convention for LLM-assisted candidate changes.

Every commit produced by this pipeline is a candidate that has only passed
automated validation, never human review -- the message format makes that
explicit and impossible to miss, regardless of which experiment produced it.
"""

REVIEW_REQUIRED_NOTE_TEMPLATE = (
    "REVIEW REQUIRED: automated candidate from the {experiment} pipeline; "
    "passed automated validation only, not human-reviewed. Do not merge without review."
)


def format_review_commit_message(experiment: str, summary: str, details: str = "") -> str:
    """summary: one-line description. details: optional extra body (e.g. source links)."""
    parts = [f"[{experiment}] {summary}"]
    if details:
        parts.append(details)
    parts.append(REVIEW_REQUIRED_NOTE_TEMPLATE.format(experiment=experiment))
    return "\n\n".join(parts)
