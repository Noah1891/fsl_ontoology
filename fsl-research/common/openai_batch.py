"""Shared OpenAI Batch API ('/v1/responses') helpers.

Used by common/submit_batches.py (upload each already-written request file
and create one batch per file), common/retrieve_batches.py (one status check
per batch per cron tick, and pulling a completed batch's raw output text),
and each experiment's own dispatch-time parsing of that raw output.
"""

import json
from pathlib import Path


def build_responses_request(
    custom_id: str,
    model: str,
    instructions: str,
    input_text: str,
    schema: dict,
    schema_name: str,
) -> dict:
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": "/v1/responses",
        "body": {
            "model": model,
            "instructions": instructions,
            "input": input_text,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        },
    }


def upload_batch_file(client, filepath: Path):
    """Upload one already-written *.jsonl request file, unmodified."""
    with open(filepath, "rb") as handle:
        return client.files.create(file=handle, purpose="batch")


def create_batch(client, file_id: str, description: str, completion_window: str = "24h"):
    return client.batches.create(
        input_file_id=file_id,
        endpoint="/v1/responses",
        completion_window=completion_window,
        metadata={"description": description},
    )


def retrieve_batch(client, batch_id: str):
    """One non-blocking status check -- the caller's cron schedule is the poll cadence."""
    return client.batches.retrieve(batch_id)


def fetch_batch_output_text(client, batch) -> str | None:
    """Raw text of a completed batch's output file, or its error file if it has no output."""
    if batch.output_file_id:
        return client.files.content(batch.output_file_id).text
    if batch.error_file_id:
        return client.files.content(batch.error_file_id).text
    return None


def extract_structured_outputs(output_text: str) -> dict:
    results: dict[str, dict] = {}
    for line in output_text.splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        custom_id = entry.get("custom_id", "<unknown>")
        if entry.get("error"):
            print(f"Batch entry {custom_id} failed: {entry['error']}")
            continue
        body = (entry.get("response") or {}).get("body") or {}
        output = body.get("output") or []
        message = next((item for item in output if item.get("type") == "message"), None)
        content = (message or {}).get("content") or []
        text_item = next((c for c in content if c.get("type") == "output_text"), None)
        if text_item is None:
            print(f"Batch entry {custom_id} has no usable output text: {body.get('error') or body}")
            continue
        results[custom_id] = json.loads(text_item["text"])
    return results
