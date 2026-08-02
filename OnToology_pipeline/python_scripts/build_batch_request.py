"""
Build and submit an OpenAI Responses API *batch* job that asks an LLM to
suggest fixes for OOPS!-detected pitfalls in the FSL ontology.

Assumes you already have these two functions available in your project:

    get_pitfall_info(path: str, pitfall_id: str) -> dict
        {
            'code': str,
            'name': str,
            'description': str,
            'importance': str,
            'num_affected_elements': str,
            'affected_elements': list[str],   # full URIs
        }

    generate_context(pitfall_num: str, input_term: str) -> str
        Generates the Turtle context (occurrences + superclasses) for a
        term like "ce:DataConcept" and returns the PATH to the resulting
        .ttl file (e.g. "context_DataConcept.ttl") — NOT the Turtle
        content itself. The file at that path must be read separately.

Import them at the top instead of the stub raises below.
"""

import json
import re
from pathlib import Path
from generate_resources import create_context_text
from extract_affected_element import get_pitfall_info


# ---------------------------------------------------------------------------
# URI <-> prefix mapping, taken from the Turtle prefixes used in FSL.
# ---------------------------------------------------------------------------
NAMESPACES = {
    "http://www.softlang.org/ontologies/ce#": "ce",
    "http://www.softlang.org/ontologies/fe#": "fe",
    "http://www.softlang.org/ontologies/le#": "le",
    "http://www.softlang.org/ontologies/pe#": "pe",
    "http://www.softlang.org/ontologies/te#": "te",
    "http://www.softlang.org/ontologies/ae#": "ae",
    "http://www.softlang.org/ontologies/ie#": "ie",
    "http://www.softlang.org/ontologies/tbox#": "tbox",
    "http://xmlns.com/foaf/0.1/": "foaf",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
    "http://www.w3.org/2000/01/rdf-schema#": "rdfs", 
    "http://www.w3.org/2001/XMLSchema#": "xsd"
}


def load_context_ttl(source_path: str, input_term: str) -> str:
    """generate_context() returns a path to a .ttl file, not the Turtle
    content itself — this reads that file and returns its text."""
    return create_context_text(source_path, input_term)


def uri_to_prefixed(uri: str) -> str:
    """'http://www.softlang.org/ontologies/ce#DataConcept' -> 'ce:DataConcept'"""
    for ns, prefix in NAMESPACES.items():
        if uri.startswith(ns):
            local = uri[len(ns):]
            return f"{prefix}:{local}"
    raise ValueError(f"No known prefix mapping for URI: {uri}")


# ---------------------------------------------------------------------------
# Structured output schema — this replaces the "please return a JSON object
# with three elements..." prose. The API enforces this shape directly.
# ---------------------------------------------------------------------------
PITFALL_FIX_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestFix": {"type": "boolean"},
        "replace": {"type": "string"},
        "with": {"type": "string"},
    },
    "required": ["suggestFix", "replace", "with"],
    "additionalProperties": False,
}

SYSTEM_INSTRUCTIONS = """You are an expert for ontology development. You will receive pitfalls detected \
in the "Foundations of Software Languages" ontology by the OOPS! tool. You get the pitfall description of \
one affected element at a time, plus all occurrences and superclasses of that element in the ontology, in \
Turtle syntax. You also receive a Markdown description of the FSL ontology for additional context.

Decide whether the pitfall should be fixed.
- If you recommend a fix: suggestFix = true, replace = the substring from the given Turtle \
context that needs to change, with = the string that should replace it.
- If you think this is a false positive: suggestFix = false, replace = "", with = ""."""


def build_user_message(pitfall: dict, element_uri: str, context_ttl: str) -> str:
    return (
        f"Pitfall ID: {pitfall['code']}\n"
        f"Pitfall Name: {pitfall['name']}\n"
        f"Pitfall Description: {pitfall['description']}\n"
        f"Affected Element: {element_uri}\n\n"
        f"### Ontology context (Turtle)\n"
        f"```turtle\n{context_ttl.strip()}\n```"
    )


def build_batch_requests(
    merged_ontology_path: str,
    oops_xml_path: str,
    pitfall_ids: list[int],
    fsl_summary_path: str,
    model: str = "gpt-5.4",
    max_num: int = None
) -> dict[list[dict]]:
    fsl_summary = Path(fsl_summary_path).read_text(encoding="utf-8")

    requests = {}
    for pid in pitfall_ids:
        request_pid = []
        pitfall = get_pitfall_info(oops_xml_path, pid)
        if pitfall is None:
            continue
        i = 0
        for element_uri in pitfall["affected_elements"]:
            if (max_num != None and i == max_num):
                break
            prefixed_term = uri_to_prefixed(element_uri)
            context_ttl = load_context_ttl(merged_ontology_path, prefixed_term)
            user_msg = build_user_message(pitfall, element_uri, context_ttl)

            custom_id = f"{pitfall['code']}__{prefixed_term.replace(':', '_')}"

            request = {
                "custom_id": custom_id,
                "method": "POST",
                "url": "/v1/responses",
                "body": {
                    "model": model,
                    "instructions": SYSTEM_INSTRUCTIONS,
                    "input": [
                        {
                            "role": "developer",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": f"### FSL ontology summary (Markdown)\n\n{fsl_summary}",
                                }
                            ],
                        },
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": user_msg}],
                        },
                    ],
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": "pitfall_fix",
                            "schema": PITFALL_FIX_SCHEMA,
                            "strict": True,
                        }
                    },
                },
            }
            request_pid.append(request)
            i += 1
        requests[pid] = request_pid
    return requests


def write_batch_file(requests: dict[list[dict]], out_path: str = "../llm_prompting/batches/batch_input"):
    for pid in requests:
        with open(out_path + f"_{pid}.jsonl", "w", encoding="utf-8") as f:
            for req in requests[pid]:
                f.write(json.dumps(req, ensure_ascii=False) + "\n")
        print(f"Written batch for pitfall ID {pid}")