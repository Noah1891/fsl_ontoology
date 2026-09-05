import json
import os
from pathlib import Path


SHACL_SYSTEM_INSTRUCTIONS = """
You are an expert ontology developer.

You will receive a SHACL validation error detected in the Foundations
of Software Languages (FSL) ontology together with the relevant ontology
context.

Analyze the violation and propose the smallest appropriate correction.

Do not invent unrelated ontology changes.
Return only the requested structured output.
""".strip()


def build_shacl_comment_request(
    focus_node,
    message,
    context_ttl,
    request_id="SHACL_COMMENT_001",
):
    model = os.environ.get("GPT_MODEL", "gpt-4.1-nano")

    input_text = (
        "SHACL violation:\n"
        f"{message}\n\n"
        "Affected element:\n"
        f"{focus_node}\n\n"
        "Ontology context (Turtle):\n"
        "```turtle\n"
        f"{context_ttl.strip()}\n"
        "```"
    )

    return {
        "custom_id": request_id,
        "method": "POST",
        "url": "/v1/responses",
        "body": {
            "model": model,
            "instructions": SHACL_SYSTEM_INSTRUCTIONS,
            "input": input_text,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "shacl_fix",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "comment": {
                                "type": "string",
                                "description": (
                                    "An appropriate rdfs:comment for "
                                    "the affected ontology class."
                                ),
                            }
                        },
                        "required": ["comment"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            },
        },
    }


def write_request(request, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(request, ensure_ascii=False) + "\n")

    print(f"Written SHACL batch request to {output_path}")
