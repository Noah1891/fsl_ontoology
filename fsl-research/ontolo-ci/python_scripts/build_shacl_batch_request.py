#!/usr/bin/env python3

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from rdflib import Graph, URIRef


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]

DEFAULT_OUTPUT = (
    REPO_ROOT
    / "fsl-research"
    / "ontolo-ci"
    / "llm_prompting"
    / "batches"
    / "shacl_requests.jsonl"
)


MARKDOWN_LINK_PATTERN = re.compile(
    r"\[[^\]]+\]\([^)]+\)"
)

URI_PATTERN = re.compile(
    r"^<[^<>]+>$"
)


SYSTEM_PROMPT = """
You repair RDF/Turtle ontology data based on SHACL validation violations.

Your task is to propose the smallest safe RDF change that fixes the reported
SHACL violation.

Rules:

1. Return ONLY valid JSON matching the required response schema.
2. Do not return Markdown.
3. Do not return explanations outside the JSON object.
4. Do not use Markdown links anywhere.
5. RDF/Turtle IRIs must be written as normal Turtle IRIs using angle brackets.
6. Never write an IRI using Markdown-link syntax.
7. Never put Markdown links inside RDF/Turtle.
8. Use concrete RDF triples only.
9. Do not use SPARQL variables in add/remove changes.
10. Do not use placeholders in add/remove changes.
11. Each RDF change must be exactly one concrete Turtle triple ending with
    a period.
12. Prefer the smallest possible repair.
13. Do not remove an existing valid triple unless the SHACL violation requires
    it.
14. Do not modify SHACL shapes to hide a violation.
15. Do not invent unrelated ontology structure.
16. Use the supplied ontology context to infer an appropriate repair.
17. If there is insufficient information to safely construct a repair, set
    skip to true.
18. If the violation can be repaired by adding one missing annotation or
    relationship that is clearly supported by the context, add only that
    triple.
19. The explanation must describe the repair briefly and accurately.
20. Never create Markdown links.

Every add/remove entry must contain one concrete Turtle triple.

The subject and predicate must be full Turtle IRIs enclosed in angle brackets.

The object may be a Turtle IRI, literal, or another valid Turtle RDF object.

Do not use Markdown syntax.

Do not use SPARQL variables.

Do not use placeholders.

If a safe repair cannot be determined, return skip=true and empty
add/remove arrays.
"""


def ensure_no_markdown_links(
    text: str,
    field_name: str,
) -> str:
    """
    Fail closed if Markdown-link syntax enters generated content.
    """

    match = MARKDOWN_LINK_PATTERN.search(text)

    if match:
        raise ValueError(
            f"{field_name} contains Markdown-link syntax: "
            f"{match.group(0)}"
        )

    return text


def validate_focus_node(
    focus_node: str,
) -> str:
    """
    Validate a SHACL focus node.
    """

    if not focus_node:
        raise ValueError(
            "Focus node is empty."
        )

    focus_node = str(
        focus_node
    ).strip()

    ensure_no_markdown_links(
        focus_node,
        "Focus node"
    )

    return focus_node


def resolve_file(
    repo_root: Path,
    path_value: str,
) -> Path:
    """
    Resolve an absolute or repository-relative path.

    IMPORTANT:
    output_path is NOT used as repo_root.
    """

    path = Path(
        path_value
    )

    if path.is_absolute():
        resolved = path
    else:
        resolved = repo_root / path

    resolved = resolved.resolve()

    if not resolved.exists():
        raise FileNotFoundError(
            f"File does not exist: {resolved}"
        )

    if not resolved.is_file():
        raise ValueError(
            f"Expected a file but found: {resolved}"
        )

    return resolved


def read_text_file(
    path: Path,
) -> str:
    """
    Read UTF-8 text and reject Markdown-link corruption.
    """

    text = path.read_text(
        encoding="utf-8"
    )

    ensure_no_markdown_links(
        text,
        f"File {path}"
    )

    return text


def read_graph(
    path: Path,
) -> Graph:
    """
    Read a Turtle/RDF file.
    """

    graph = Graph()

    graph.parse(
        str(path),
        format="turtle"
    )

    return graph


def build_context(
    data_file: Path,
    focus_node: str,
    max_triples: int = 150,
) -> str:
    """
    Build RDF context around the SHACL focus node.
    """

    graph = read_graph(
        data_file
    )

    focus_uri = URIRef(
        focus_node
    )

    relevant: List[str] = []

    for subject, predicate, obj in graph.triples(
        (focus_uri, None, None)
    ):
        triple = (
            f"<{subject}> "
            f"<{predicate}> "
            f"{obj.n3(graph.namespace_manager)} ."
        )

        if triple not in relevant:
            relevant.append(
                triple
            )

    for subject, predicate, obj in graph.triples(
        (None, None, focus_uri)
    ):
        triple = (
            f"<{subject}> "
            f"<{predicate}> "
            f"{obj.n3(graph.namespace_manager)} ."
        )

        if triple not in relevant:
            relevant.append(
                triple
            )

    if len(relevant) < max_triples:

        for subject, predicate, obj in graph:

            triple = (
                f"<{subject}> "
                f"<{predicate}> "
                f"{obj.n3(graph.namespace_manager)} ."
            )

            if triple not in relevant:
                relevant.append(
                    triple
                )

            if len(relevant) >= max_triples:
                break

    context = "\n".join(
        relevant[:max_triples]
    )

    ensure_no_markdown_links(
        context,
        "Ontology context"
    )

    return context


def validate_rdf_triple(
    triple: str,
    field_name: str,
) -> str:
    """
    Validate one concrete RDF/Turtle triple.
    """

    if not isinstance(
        triple,
        str
    ):
        raise ValueError(
            f"{field_name} must be a string."
        )

    triple = triple.strip()

    if not triple:
        raise ValueError(
            f"{field_name} is empty."
        )

    ensure_no_markdown_links(
        triple,
        field_name
    )

    if "?" in triple:
        raise ValueError(
            f"{field_name} contains a SPARQL variable: "
            f"{triple}"
        )

    if not triple.endswith("."):
        raise ValueError(
            f"{field_name} must end with a period: "
            f"{triple}"
        )

    parts = triple.split(
        None,
        2
    )

    if len(parts) != 3:
        raise ValueError(
            f"{field_name} is not a complete RDF triple: "
            f"{triple}"
        )

    subject = parts[0]
    predicate = parts[1]

    if not URI_PATTERN.fullmatch(
        subject
    ):
        raise ValueError(
            f"{field_name} has an invalid subject: "
            f"{triple}"
        )

    if not URI_PATTERN.fullmatch(
        predicate
    ):
        raise ValueError(
            f"{field_name} has an invalid predicate: "
            f"{triple}"
        )

    test_graph = Graph()

    turtle = (
        "@prefix rdf: "
        "<http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
        f"{triple}\n"
    )

    try:
        test_graph.parse(
            data=turtle,
            format="turtle"
        )
    except Exception as exc:
        raise ValueError(
            f"{field_name} is not valid Turtle: "
            f"{triple}"
        ) from exc

    if len(test_graph) != 1:
        raise ValueError(
            f"{field_name} must contain exactly one RDF triple: "
            f"{triple}"
        )

    return triple


def build_single_request(
    violation: Dict[str, Any],
    repo_root: Path,
) -> Dict[str, Any]:
    """
    Build one OpenAI batch request.
    """

    focus_node = validate_focus_node(
        str(
            violation.get(
                "focus_node",
                ""
            )
        )
    )

    message = str(
        violation.get(
            "message",
            ""
        )
    ).strip()

    shape_path_value = str(
        violation.get(
            "shape",
            ""
        )
    ).strip()

    data_file_value = str(
        violation.get(
            "data_file_path"
        )
        or violation.get(
            "data_file"
        )
        or ""
    ).strip()

    if not message:
        raise ValueError(
            "Violation message is empty."
        )

    if not shape_path_value:
        raise ValueError(
            f"No SHACL shape path supplied for "
            f"violation of {focus_node}."
        )

    if not data_file_value:
        raise ValueError(
            f"No data file supplied for "
            f"violation of {focus_node}."
        )

    ensure_no_markdown_links(
        message,
        "Violation message"
    )

    shape_path = resolve_file(
        repo_root,
        shape_path_value
    )

    data_file = resolve_file(
        repo_root,
        data_file_value
    )

    shape_text = read_text_file(
        shape_path
    )

    context = build_context(
        data_file=data_file,
        focus_node=focus_node,
    )

    if not context.strip():
        raise ValueError(
            f"Ontology context is empty for "
            f"focus node {focus_node}."
        )

    user_input = f"""
SHACL violation:

Focus node:
{focus_node}

Violation message:
{message}

SHACL shape:

{shape_text}

Ontology context:

{context}

Task:

Determine the smallest safe RDF/Turtle change required to fix this
SHACL violation.

Return the repair as concrete RDF triples.

The subject and predicate in every add/remove entry must use full Turtle
IRI syntax with angle brackets.

Do not use Markdown links.

Do not use SPARQL variables.

Do not use placeholders.

Do not modify the SHACL shape.

If the violation cannot be repaired safely from the supplied information,
return skip=true and empty add/remove arrays.
""".strip()

    ensure_no_markdown_links(
        user_input,
        "Generated user prompt"
    )

    request_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "add": {
                "type": "array",
                "items": {
                    "type": "string"
                }
            },
            "remove": {
                "type": "array",
                "items": {
                    "type": "string"
                }
            },
            "explanation": {
                "type": "string"
            },
            "skip": {
                "type": "boolean"
            }
        },
        "required": [
            "add",
            "remove",
            "explanation",
            "skip"
        ]
    }

    return {
        "custom_id": (
            f"shacl-{focus_node}"
        ),
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": "gpt-5-mini",
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT.strip(),
                },
                {
                    "role": "user",
                    "content": user_input,
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "shacl_repair",
                    "strict": True,
                    "schema": request_schema,
                },
            },
        },
    }


def build_batch_requests(
    violations: List[Dict[str, Any]],
    output_path: Path,
) -> List[Dict[str, Any]]:
    """
    Build batch requests.

    This signature intentionally matches run_validation.py:

        build_batch_requests(
            all_violation_records,
            output_path
        )

    output_path is only used to determine where the JSONL will be written.
    It is NOT treated as the repository root.
    """

    requests: List[Dict[str, Any]] = []

    for index, violation in enumerate(
        violations,
        start=1
    ):
        try:
            request = build_single_request(
                violation=violation,
                repo_root=REPO_ROOT,
            )

            requests.append(
                request
            )

        except Exception as exc:
            raise RuntimeError(
                f"Failed to build request {index}: "
                f"{exc}"
            ) from exc

    output_path = Path(
        output_path
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with output_path.open(
        "w",
        encoding="utf-8"
    ) as handle:

        for request in requests:

            line = json.dumps(
                request,
                ensure_ascii=False,
                separators=(",", ":"),
            )

            ensure_no_markdown_links(
                line,
                "Generated JSONL request"
            )

            handle.write(
                line + "\n"
            )

    return requests


def load_violations(
    input_file: Path,
) -> List[Dict[str, Any]]:
    """
    Load SHACL violations from JSON.
    """

    with input_file.open(
        "r",
        encoding="utf-8"
    ) as handle:
        data = json.load(
            handle
        )

    if isinstance(
        data,
        list
    ):
        return data

    if isinstance(
        data,
        dict
    ):

        if "violations" in data:

            violations = data["violations"]

            if not isinstance(
                violations,
                list
            ):
                raise ValueError(
                    "'violations' must be a list."
                )

            return violations

        if "focus_node" in data:
            return [data]

    raise ValueError(
        "Input JSON must be either a list of violations "
        "or an object containing a 'violations' list."
    )


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Build OpenAI batch requests from SHACL violations."
        )
    )

    parser.add_argument(
        "--input",
        required=True,
        help="JSON file containing SHACL violations.",
    )

    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output JSONL file.",
    )

    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="Repository root.",
    )

    args = parser.parse_args()

    repo_root = Path(
        args.repo_root
    ).resolve()

    input_file = resolve_file(
        repo_root,
        args.input
    )

    output_file = Path(
        args.output
    )

    if not output_file.is_absolute():
        output_file = (
            repo_root / output_file
        )

    output_file = output_file.resolve()

    violations = load_violations(
        input_file
    )

    requests = build_batch_requests(
        violations,
        output_file
    )

    print(
        f"Written {len(requests)} SHACL batch requests to "
        f"{output_file}"
    )


if __name__ == "__main__":
    main()
