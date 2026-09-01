"""Shared OpenAI Batch API ('/v1/responses') helpers.

Used by the LLM step of both saref-experiment/versioning and
ontoology: build one structured-output request, submit/poll a
batch job, and pull the structured JSON payload back out of its output file.
"""

import json


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


def submit_batch(client, requests: list[dict], completion_window: str = "24h"):
    batch_input = "\n".join(json.dumps(r) for r in requests) + "\n"
    batch_file = client.files.create(
        file=("batch.jsonl", batch_input.encode("utf-8")), purpose="batch"
    )
    return client.batches.create(
        input_file_id=batch_file.id, endpoint="/v1/responses", completion_window=completion_window
    )


class BatchNotFinished(Exception):
    pass


def _poll_once(client, batch_id: str):
    batch = client.batches.retrieve(batch_id)
    if batch.status in ("completed", "failed", "expired", "cancelled"):
        return batch
    print(f"  status: {batch.status}")
    raise BatchNotFinished()


def poll_batch(client, batch_id: str):
    """Poll a batch job until it reaches a terminal status, with exponential backoff."""
    from tenacity import retry, retry_if_exception_type, stop_after_delay, wait_exponential

    poll = retry(
        retry=retry_if_exception_type(BatchNotFinished),
        wait=wait_exponential(multiplier=2, min=5, max=300),
        stop=stop_after_delay(60 * 60),
        reraise=True,
    )(_poll_once)
    return poll(client, batch_id)


def extract_structured_outputs(output_text: str) -> dict:
    """Parse a Batch output file's lines into {custom_id: structured_payload}."""
    results: dict[str, dict] = {}
    for line in output_text.splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        custom_id = entry["custom_id"]
        if entry.get("error"):
            raise RuntimeError(f"Batch entry {custom_id} failed: {entry['error']}")
        body = entry["response"]["body"]
        structured_text = body["output"][0]["content"][0]["text"]
        results[custom_id] = json.loads(structured_text)
    return results
