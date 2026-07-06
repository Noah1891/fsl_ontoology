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
from extract_element_context import generate_context, run_robot
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


def load_context_ttl(pitfall_num: str, input_term: str) -> str:
    """generate_context() returns a path to a .ttl file, not the Turtle
    content itself — this reads that file and returns its text."""
    plain_num = re.sub(r"^P0*", "", pitfall_num, flags=re.IGNORECASE) or "0"
    context_path = generate_context(plain_num, input_term)
    return Path(context_path).read_text(encoding="utf-8")


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
- If you recommend a fix: suggestFix = true, replace = the minimal exact substring from the given Turtle \
context that needs to change, with = the string that should replace it.
- If you think this is a false positive: suggestFix = false, replace = "", with = "".

Keep "replace" minimal and make sure it is an exact substring of the provided Turtle context so it can be \
applied with a simple string replacement."""


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
    oops_xml_path: str,
    pitfall_ids: list[int],
    fsl_summary_path: str,
    model: str = "gpt-5.4",
    max_num: int = None
) -> list[dict]:
    fsl_summary = Path(fsl_summary_path).read_text(encoding="utf-8")

    requests = []
    for pid in pitfall_ids:
        pitfall = get_pitfall_info(oops_xml_path, pid)
        i = 0
        for element_uri in pitfall["affected_elements"]:
            if (max_num != None and i == max_num):
                break
            prefixed_term = uri_to_prefixed(element_uri)
            context_ttl = load_context_ttl(pitfall["code"], prefixed_term)
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
            requests.append(request)
            i += 1
    return requests


def write_batch_file(requests: list[dict], out_path: str = "../llm_prompting/batches/batch_input.jsonl") -> str:
    with open(out_path, "w", encoding="utf-8") as f:
        for req in requests:
            f.write(json.dumps(req, ensure_ascii=False) + "\n")
    return out_path

def custom_id_to_prefix(custom_id: str) -> str:
    """'P08__ce_DataConcept' -> 'ce'"""
    _, term_part = custom_id.split("__", 1)
    prefix, _ = term_part.split("_", 1)
    return prefix

def _build_whitespace_insensitive_pattern(text: str) -> re.Pattern:
    """Builds a regex that matches `text` while treating any run of
    whitespace (spaces, tabs, newlines) as equivalent to any other run
    of whitespace. Needed because LLM-returned 'replace' strings are
    usually single-line/compact, while ROBOT-serialized Turtle wraps
    long statements across multiple indented lines.
 
    e.g. "a ; b ." also matches "a ;\n    b ." in the target file.
    """
    tokens = text.strip().split()
    pattern = r"\s+".join(re.escape(tok) for tok in tokens)
    return re.compile(pattern, flags=re.DOTALL)
 
 
def apply_fixes(results: dict[str, dict], ontology_dir: str = "../../ontologies/punned") -> None:
    """Applies each suggested fix from parse_batch_results() to the
    corresponding <prefix>_punned.ttl file, then runs `robot convert`
    on that file (in place) to normalize/re-serialize it.
 
    results: output of parse_batch_results(), i.e.
        {custom_id: {"suggestFix": bool, "replace": str, "with": str}}
    """
    ontology_dir = Path(ontology_dir)
 
    for custom_id, fix in results.items():
        if not fix.get("suggestFix"):
            print(f"[{custom_id}] suggestFix=false, skipping.")
            continue
 
        replace_str = fix.get("replace", "")
        with_str = fix.get("with", "")
        if not replace_str:
            print(f"[{custom_id}] suggestFix=true but 'replace' is empty, skipping.")
            continue
 
        prefix = custom_id_to_prefix(custom_id)
        ttl_path = ontology_dir / f"{prefix}_punned.ttl"
 
        if not ttl_path.exists():
            print(f"[{custom_id}] File not found: {ttl_path}, skipping.")
            continue

        # The punned files declare the module's own prefix as the DEFAULT
        # namespace (e.g. "@prefix : <...ce#> ."), so terms appear as
        # ":Foo" rather than "ce:Foo" inside the file. The LLM was shown
        # fully-prefixed terms though, and tends to echo them back as
        # "ce:Foo" in its replace/with strings. Normalize that away
        # before matching, otherwise the search silently fails.
        own_prefix_pattern = re.compile(rf"\b{re.escape(prefix)}:")
        replace_str = own_prefix_pattern.sub(":", replace_str)
        with_str = own_prefix_pattern.sub(":", with_str)
 
        content = ttl_path.read_text(encoding="utf-8")
        pattern = _build_whitespace_insensitive_pattern(replace_str)
        match = pattern.search(content)
        if match is None:
            print(f"[{custom_id}] 'replace' pattern not found in {ttl_path}, skipping.")
            continue
 
        # Use a function (not a plain string) as repl so backslashes/group
        # refs accidentally contained in `with_str` are treated literally.
        updated = pattern.sub(lambda _m: with_str, content, count=1)
        ttl_path.write_text(updated, encoding="utf-8")
        print(f"[{custom_id}] Replaced text in {ttl_path}.")

        convert_command = [
            "robot", "convert",
            "--input", str(ttl_path),
            "--format", "ttl",
            "--output", str(ttl_path),
        ]
 
        run_robot(convert_command, "Error during ROBOT convert. Aborting.",())


# ---------------------------------------------------------------------------
# Submission + result parsing (uses the official `openai` Python SDK)
# ---------------------------------------------------------------------------
def submit_batch(jsonl_path: str):
    from openai import OpenAI

    client = OpenAI()
    batch_file = client.files.create(file=open(jsonl_path, "rb"), purpose="batch")
    batch = client.batches.create(
        input_file_id=batch_file.id,
        endpoint="/v1/responses",
        completion_window="24h",
    )
    return batch


def parse_batch_results(client, output_file_id: str) -> dict[str, dict]:
    """Returns {custom_id: {'suggestFix': ..., 'replace': ..., 'with': ...}}"""
    raw = client.files.content(output_file_id).text
    results = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        custom_id = obj["custom_id"]
        body = obj["response"]["body"]

        # Find the assistant message's structured JSON text among the output items.
        text_payload = None
        for item in body.get("output", []):
            if item.get("type") == "message":
                for c in item.get("content", []):
                    if c.get("type") == "output_text":
                        text_payload = c["text"]
                        break
        if text_payload is not None:
            results[custom_id] = json.loads(text_payload)
    return results


if __name__ == "__main__":
    # Example usage — adjust paths and pitfall IDs to your setup.
    reqs = build_batch_requests(
        oops_xml_path="../oops_prompting/report/oops_report.xml",
        pitfall_ids=[8],
        fsl_summary_path="../llm_prompting/system_message/FSLsummary.md",
        max_num=5
    )
    path = write_batch_file(reqs, "../llm_prompting/batches/batch_input_8.jsonl")
    print(f"Wrote {len(reqs)} requests to {path}")

    # Uncomment to actually submit:
    # batch = submit_batch(path)
    # print("Submitted batch:", batch.id)

    """ outputs = {
        "P08__ce_HigherOrderConcept" : {
                                    "suggestFix": True,
                                    "replace": "ce:HigherOrderConcept rdf:type owl:Class ;\n                      rdfs:subClassOf ce:PrimitiveConcept .",
                                    "with": "ce:HigherOrderConcept rdf:type owl:Class ;\n                      rdfs:subClassOf ce:PrimitiveConcept ;\n                      rdfs:label \"Higher-order concept\"@en ;\n                      rdfs:comment \"A language concept that treats functions, modules, types, or other entities as first-class values or allows abstraction over them.\"@en ."
                                }
        }

    apply_fixes(outputs) """