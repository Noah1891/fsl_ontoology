import json

from openai import OpenAI
from pathlib import Path
from tenacity import (
    retry,
    retry_if_exception_type,
    wait_exponential,
    stop_after_delay
)

class BatchesNotFinished(Exception):
    pass

client = OpenAI()

def upload_batch_files(dir: str) -> list:
    batch_input_files = []
    for filepath in Path(dir).iterdir():
        with open(filepath, "rb") as f:
            batch_input_file = client.files.create(
                file=f,
                purpose='batch'
            )
        batch_input_files.append(batch_input_file)
    return batch_input_files

def create_batch_jobs(batch_input_files: list) -> list:
    batches = []
    for batch_input_file in batch_input_files:
        batch = client.batches.create(
            input_file_id=batch_input_file.id,
            endpoint="/v1/responses",
            completion_window="24h",
            metadata={"description": f"{batch_input_file.filename}"}
        )
        batches.append(batch)
    return batches


@retry(
    retry=retry_if_exception_type(BatchesNotFinished),
    wait=wait_exponential(multiplier=2, min=5, max=300),
    stop=stop_after_delay(60 * 60),
    reraise=True,
)
def retrieve_batches(
    batches,
    response_files: dict[str, object],
    finished: set[str],
):
    for batch in batches:
        if batch.id in finished:
            continue

        current = client.batches.retrieve(batch.id)
        file_name = current.metadata["description"]
        match current.status:
            case "completed":
                if current.output_file_id:
                    response_files[file_name] = client.files.content(current.output_file_id)
                elif current.error_file_id:
                    response_files[f"error_{file_name}"] = client.files.content(current.error_file_id)
                finished.add(current.id)

            case "failed" | "expired" | "cancelled":
                print(f"{current.id}: {current.status}")
                finished.add(current.id)

            case "validating":
                print(f"{current.id} is validating")

            case "finalizing":
                print(f"{current.id} is finalizing")

            case "cancelling":
                print(f"{current.id} is cancelling")

            case "in_progress":
                print(f"{current.id} is in progress")

    if len(finished) != len(batches):
        raise BatchesNotFinished()

    return response_files

def write_output_files(response_files: dict, dir: str):
    dir = Path(dir)
    dir.mkdir(parents=True, exist_ok=True)
    for response_file_name in response_files:
        file_id_path = dir / f"output_{response_file_name}"
        file_id_path.write_text(response_files[response_file_name].text, encoding='utf-8')

if __name__ == '__main__':
    batch_input_files=upload_batch_files('../llm_prompting/batches')
    batches = create_batch_jobs(batch_input_files)
    response_files = {}
    finished = set()
    try:
        responses = retrieve_batches(batches, response_files, finished)
        write_output_files(responses, '../llm_prompting/outputs')
    except BatchesNotFinished:
        print("Could not retrieve batches after trying for one hour.")
    finally:
        batch_ids = [batch.id for batch in batches]
        with open("../llm_prompting/outputs/batch_ids.json", "w", encoding="utf-8") as f:
            json.dump(batch_ids, f, indent=4)
    