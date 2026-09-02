import json
from pathlib import Path
import hashlib
from rdflib import URIRef

from turtle_file import parse_turtle
from extract_pitfall_info import get_pitfall_info
from generate_context import create_contexts


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
    "http://www.w3.org/2001/XMLSchema#": "xsd",
    "http://www.w3.org/2004/02/skos/core#": "skos",
    "http://www.w3.org/2002/07/owl#": "owl",
    "http://www.w3.org/2006/time#": "time"
}

def uri_to_prefixed(uri: str) -> str:
    """'http://www.softlang.org/ontologies/ce#DataConcept' -> 'ce:DataConcept'"""
    for ns, prefix in NAMESPACES.items():
        if uri.startswith(ns):
            local = uri[len(ns):]
            return f"{prefix}:{local}"
    raise ValueError(f"No known prefix mapping for URI: {uri}")


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
            """Based on the provided usages of the affected terms, decide whether the concept of it should be split into multiple concepts or the detected pitfall is a false positive. \
            Provide the blocks for the new concepts resulting from the split and then assign the concepts that used the old concept to one of the new ones. \
            Decide which of the relationships of the original concept (rdfs:subClassOf, foaf:page, etc.) are carried over to which of the newly created concept blocks. \
            The relationships do not have be partioned disjunctly. Labels and comments are newly generated."""  
            ])
        case "P08":
            inst = inst.join([
            "",
            "Based on the provided context of the affected term, generate a label and or a comment describing it depending on what is missing. If both are missing, generate both. If one of them is present, generate the other one.",
            ])
        case "P13":
            inst = inst.join([
            "",
            "Based on the provided OWL properties of the ontology, decide whether some of the existing properties are an inverse to the affected element. If yes, return the CURIE of the inverse element.",
            ])
        case "P34":
            inst = inst.join([
            "",
            "Based on the provided usages of the affected term, decide whether the element is missing a class declaration. A false positive may occure if a class declaration is actually present or the elements have been imported."
            ])
    return inst


def build_user_message(element_uris: list, context_ttl: str) -> str:
    return (
        f"Affected Elements: {[uri_to_prefixed(str(elem)) for elem in element_uris]}\n\n"
        f"### Ontology context (Turtle)\n"
        f"```turtle\n{context_ttl.strip()}\n```"
    )


def get_output_schema(pitfall_code: str) -> dict:
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
                    "concepts": {
                        "type": "array",
                        "description": "One entry per affected class in the batch.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "oldConceptId": {
                                    "type": "string",
                                    "description": "CURIE of the original class."
                                },
                                "split": {
                                    "type": "boolean",
                                    "description": "true if this class should be split; false in case of a false positive."
                                },
                                "newConcepts": {
                                    "type": "array",
                                    "description": "Empty if split=false.",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "conceptId": {"type": "string"},
                                            "block": {"type": "string", "description": "Full Turtle block."}
                                        },
                                        "required": ["conceptId", "block"],
                                        "additionalProperties": False
                                    }
                                }
                            },
                            "required": ["oldConceptId", "split", "newConcepts"],
                            "additionalProperties": False
                        }
                    },
                    "assignments": {
                        "type": "array",
                        "description": "All relevant usage edges: both external (already known/resolved CURIEs) and internal (between concepts newly created in this call).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "sourceConcept": {
                                    "type": "string",
                                    "description": "CURIE of the consuming side. Either an already known CURIE (external non-P07 class, or new ID of an already processed P07 neighbor passed via context), OR one of the conceptId values newly created above under 'concepts' if the consuming class itself is part of this batch."
                                },
                                "property": {"type": "string"},
                                "targetOldConceptId": {
                                    "type": "string",
                                    "description": "Which oldConceptId from 'concepts' was originally referenced here."
                                },
                                "assignedConceptId": {
                                    "type": "string",
                                    "description": "Must be one of the conceptId values under the matching oldConceptId (or equal to targetOldConceptId if split=false)."
                                }
                            },
                            "required": ["sourceConcept", "property", "targetOldConceptId", "assignedConceptId"],
                            "additionalProperties": False
                        }
                    }
                },
                "required": ["concepts", "assignments"],
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
                        "description": "true if a OWL property exists that fits as the inverse to the affected element. False otherwise"
                    },
                    "inverse": {
                        "type": "string",
                        "description": "The CURIE of the existing OWL property that fits as an inverse element to the affected element. Empty string if exists=false."
                    }
                },
                "required": ["exists", "inverse"],
                "additionalProperties": False
            }
        case "P34":
            schema = {
                "type": "object",
                "properties": {
                    "missing": {
                        "type": "boolean",
                        "description": "true if the affected element is really missing a class declaration. False otherwise."
                    },
                },
                "required": ["missing"],
                "additionalProperties": False
            }
    return schema


def build_request_payload(
    id: str,
    pitfall: dict,
    element_uris: list,
    context_ttl: str,
    fsl_summary: str,
    model: str,
) -> dict:
    extra_instructions = build_case_specific_instructions(pitfall)
    instructions = SYSTEM_INSTRUCTIONS_BASE
    if extra_instructions:
        instructions = f"{instructions}\n\n{extra_instructions}".strip()

    user_msg = build_user_message(element_uris, context_ttl)
    output_schema = get_output_schema(pitfall['code'])

    return {
        "custom_id": id,
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


def make_batch_id(pitfall_code: str, batch_terms: list, index: int) -> str:
    """Creates reproduceable batch id.
    """
    sorted_terms = sorted(str(t) for t in batch_terms)
    content = "|".join(sorted_terms)
    short_hash = hashlib.sha1(content.encode("utf-8")).hexdigest()[:8]
    return f"{pitfall_code}_{index:03d}_{short_hash}"


def build_batch_requests(
    merged_ontology_path: Path,
    oops_xml_path: str,
    pitfall_ids: list[int],
    fsl_summary_path: Path,
    model: str = "gpt-4.1-nano"
) -> dict[list[dict]]:
    fsl_summary = fsl_summary_path.read_text(encoding="utf-8")

    requests = {}
    tf = parse_turtle(merged_ontology_path)
    for pid in pitfall_ids:
        request_pid = []
        pitfall = get_pitfall_info(oops_xml_path, pid)
        if pitfall is None:
            continue
        affected_elements = {URIRef(ae) for ae in pitfall['affected_elements']}
        contexts, element_uris = create_contexts(tf, affected_elements, pitfall['code'])
        for i, context in enumerate(contexts):
            custom_id = make_batch_id(pitfall['code'], element_uris[i], i)
            request = build_request_payload(
                id=custom_id,
                pitfall=pitfall,
                element_uris=element_uris[i],
                context_ttl=context,
                fsl_summary=fsl_summary,
                model=model,
            )
            request_pid.append(request)
        requests[pid] = request_pid
    return requests


def write_batch_file(requests: dict[list[dict]], out_path: str = "../llm_prompting/batches/batch_input"):
    for pid in requests:
        with open(out_path + f"_{pid}.jsonl", "w", encoding="utf-8") as f:
            for idx, req in enumerate(requests[pid]):
                line = json.dumps(req, ensure_ascii=False)
                if idx < len(requests[pid]) - 1:
                    line += "\n"
                f.write(line)
        print(f"Written batch for pitfall ID {pid}") 
