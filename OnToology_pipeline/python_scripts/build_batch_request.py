import json
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

def load_context_ttl(source_path: str, input_term: str, pitfall: dict) -> str:
    """generate_context() returns a path to a .ttl file, not the Turtle
    content itself — this reads that file and returns its text."""
    return create_context_text(source_path, input_term, pitfall['code'])


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

PITFALL_FIX_SCHEMA_FALLBACK = PITFALL_FIX_SCHEMA

SYSTEM_INSTRUCTIONS_BASE = """You are an expert for ontology development. You will receive pitfalls detected \
in the "Foundations of Software Languages" ontology by the OOPS! tool. You get the pitfall description of \
one affected element at a time plus a snippet of the ontology providing you the relevant context to potentially fix the pitfall. \
You'll also receive a Markdown description of the FSL ontology for additional context."""


def build_case_specific_instructions(pitfall: dict) -> str:
    """Return any extra instructions that should depend on the pitfall code.

    Extend this method with case distinctions for specific pitfall IDs.
    """
    pitfall_code = pitfall["code"]
    pitfall_name = str(pitfall.get("name", ""))
    pitfall_description = str(pitfall.get("description", ""))

    header_lines = [
        f"Pitfall ID: {pitfall_code}",
        f"Pitfall Name: {pitfall_name}",
        f"Pitfall Description: {pitfall_description}",
    ]

    inst = "\n".join(header_lines)
    match pitfall_code:
        case "P04":
            inst = inst.join([
            "",
            "Based on the provided usages of the isolated term, decide whether it can be removed from the ontology entirely or the detected pitfall is a false positive.",
            ])
        case "P07":
            inst = inst.join([
            "",
            """Based on the provided subclasses of the affected term, decide whether the concept of it should be split into multiple concepts or the detected pitfall is a false positive. \
            Provide the blocks for the new concepts resulting from the split and then assign the subclasses to one them."""  
            ])
        case "P08":
            inst = inst.join([
            "",
            "Based on the provided context of the affected term, generate a label and or a comment describing it depending on what is missing. If both are missing, generate both. If one of them is present, generate the other one.",
            ])
        case "P13":
            inst = inst.join([
            "",
            "Based on the provided owl:Properties of the ontology, decide whether some of the existing properties is an inverse to the affected element. If yes, return the CURIE of the inverse element.",
            ])
    return inst


def build_user_message(element_uri: str, context_ttl: str) -> str:
    return (
        f"Affected Element: {element_uri}\n\n"
        f"### Ontology context (Turtle)\n"
        f"```turtle\n{context_ttl.strip()}\n```"
    )


def get_output_schema(pitfall: dict) -> dict:
    pitfall_code = pitfall["code"]
    schema = {}
    match pitfall_code:
        case "P04":
            schema = {
                "type": "object",
                "properties": {
                    "remove": {
                        "type": "boolean",
                        "description": "true if the pitfall is real and the term should be removed; false if it is a false positive."
                    }
                },
                "required": ["remove"],
                "additionalProperties": False,
            }
        case "P07":
            schema = {
                "type": "object",
                "properties": {
                    "split": {
                        "type": "boolean",
                        "description": "true if the pitfall is real and the concept should be split; false if it is a false positive."
                    },
                    "newConcepts": {
                        "type": "array",
                        "description": "The new concept blocks, if split=true. Empty if split=false.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "conceptId": {
                                    "type": "string",
                                    "description": "Unique identifier/CURIE of the new concept, referenced by subclassAssignments."
                                },
                                "block": {
                                    "type": "string",
                                    "description": "The full Turtle block of the new concept."
                                }
                            },
                            "required": ["conceptId", "block"],
                            "additionalProperties": False
                        }
                    },
                    "subclassAssignments": {
                        "type": "array",
                        "description": "Assignment of each affected subclass to exactly one of the new concepts. Empty if split=false.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "subclass": {
                                    "type": "string",
                                    "description": "CURIE of the subclass."
                                },
                                "assignedConceptId": {
                                    "type": "string",
                                    "description": "Must match one of the conceptId values in newConcepts."
                                }
                            },
                            "required": ["subclass", "assignedConceptId"],
                            "additionalProperties": False
                        }
                    }
                },
                "required": ["split", "newConcepts", "subclassAssignments"],
                "additionalProperties": False
            }
        case "P08":
            schema = {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "The generated rdfs:label for the term. Empty string if a label was already present in the context and did not need to be generated."
                    },
                    "comment": {
                        "type": "string",
                        "description": "The generated rdfs:comment for the term. Empty string if a comment was already present in the context and did not need to be generated."
                    }
                },
                "required": ["label", "comment"],
                "additionalProperties": False
            }
        case "P13":
            schema = {
                "type": "object",
                "properties": {
                    "exists": {
                        "type": "boolean",
                        "description": "true if a owl:Property exists that fits as the inverse to the affected element. False otherwise"
                    },
                    "inverse": {
                        "type": "string",
                        "description": "The CURIE of the existing owl:Property that fits as an inverse element to the affected element."
                    }
                },
                "required": ["exists", "inverse"],
                "additionalProperties": False
            }
        case _:
            schema = PITFALL_FIX_SCHEMA_FALLBACK
    return schema


def build_request_payload(
    pitfall: dict,
    element_uri: str,
    context_ttl: str,
    fsl_summary: str,
    model: str,
) -> dict:
    extra_instructions = build_case_specific_instructions(pitfall)
    instructions = SYSTEM_INSTRUCTIONS_BASE
    if extra_instructions:
        instructions = f"{instructions}\n\n{extra_instructions}".strip()

    user_msg = build_user_message(element_uri, context_ttl)
    output_schema = get_output_schema(pitfall)

    return {
        "method": "POST",
        "url": "/v1/responses",
        "body": {
            "model": model,
            "instructions": instructions,
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
                    "content": [
                        {
                            "type": "input_text",
                            "text": user_msg,
                        }
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "pitfall_fix",
                    "schema": output_schema,
                    "strict": True,
                }
            },
        },
    }


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
            context_ttl = load_context_ttl(merged_ontology_path, prefixed_term, pitfall)
            custom_id = f"{pitfall['code']}__{prefixed_term.replace(':', '_')}"
            request = build_request_payload(
                pitfall=pitfall,
                element_uri=element_uri,
                context_ttl=context_ttl,
                fsl_summary=fsl_summary,
                model=model,
            )
            request["custom_id"] = custom_id
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