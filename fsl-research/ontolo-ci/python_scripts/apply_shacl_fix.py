import argparse
import json
import re
from pathlib import Path

from rdflib import Graph


FULL_URI_TRIPLE = re.compile(
    r"^\s*<[^<>]+>\s+<[^<>]+>\s+.+\s+\.\s*$"
)


def load_jsonl(path):
    """Load non-empty JSONL lines."""
    records = []

    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if line:
                records.append(json.loads(line))

    return records


def extract_output_text(result):
    """Extract structured model output from an OpenAI Responses batch result."""
    response = result.get("response", {})
    body = response.get("body", {})
    output = body.get("output", [])

    for item in output:
        if item.get("type") != "message":
            continue

        for content in item.get("content", []):
            if content.get("type") != "output_text":
                continue

            text = content.get("text")

            if text:
                return text

    raise ValueError(
        f"Could not find output_text in batch result: "
        f"{result.get('custom_id')}"
    )


def validate_rdf_changes(triples, field_name):
    """
    Validate every LLM-generated change before applying it.

    Each change must:
    - be a string
    - contain exactly one concrete Turtle triple
    - use full URIs for subject and predicate
    - not contain Markdown links
    - not contain SPARQL variables
    - parse successfully as standalone Turtle
    """
    if not isinstance(triples, list):
        raise ValueError(
            f"'{field_name}' must be an array."
        )

    for index, triple in enumerate(triples):
        if not isinstance(triple, str):
            raise ValueError(
                f"{field_name}[{index}] must be a string."
            )

        if not triple.strip():
            raise ValueError(
                f"{field_name}[{index}] is empty."
            )

        if re.search(
            r"\[[^\]]+\]\([^)]+\)",
            triple,
        ):
            raise ValueError(
                f"{field_name}[{index}] contains a Markdown link: "
                f"{triple}"
            )

        if "?" in triple:
            raise ValueError(
                f"{field_name}[{index}] contains a SPARQL variable: "
                f"{triple}"
            )

        if not FULL_URI_TRIPLE.match(triple):
            raise ValueError(
                f"{field_name}[{index}] is not a concrete "
                f"full-URI Turtle triple: {triple}"
            )

        graph = Graph()

        try:
            graph.parse(
                data=triple,
                format="turtle",
            )
        except Exception as exc:
            raise ValueError(
                f"{field_name}[{index}] is invalid Turtle: "
                f"{triple}\n{exc}"
            )

        if len(graph) != 1:
            raise ValueError(
                f"{field_name}[{index}] must contain exactly "
                f"one RDF triple: {triple}"
            )


def parse_turtle_triples(triples):
    """Parse validated concrete RDF/Turtle triples into an RDF graph."""
    graph = Graph()

    for triple in triples:
        temporary_graph = Graph()

        temporary_graph.parse(
            data=triple,
            format="turtle",
        )

        for rdf_triple in temporary_graph:
            graph.add(rdf_triple)

    return graph


def get_target_file(request, repo_root):
    """
    Read the target ontology file from the batch request.

    Expected request input:

        Target ontology file:
        ontologies/tbox.ttl
    """
    body = request.get("body", {})
    input_text = body.get("input", "")

    marker = "Target ontology file:\n"

    if marker not in input_text:
        raise ValueError(
            "Batch request does not contain "
            "'Target ontology file:'"
        )

    target = input_text.split(
        marker,
        1,
    )[1].split(
        "\n\n",
        1,
    )[0].strip()

    if not target:
        raise ValueError(
            "Target ontology file is empty."
        )

    target_path = Path(target)

    if target_path.is_absolute():
        raise ValueError(
            f"Absolute ontology paths are not allowed: "
            f"{target}"
        )

    target_path = (
        Path(repo_root) / target_path
    ).resolve()

    repo_root = Path(repo_root).resolve()

    try:
        target_path.relative_to(repo_root)
    except ValueError:
        raise ValueError(
            f"Target ontology is outside repository: "
            f"{target_path}"
        )

    if not target_path.exists():
        raise FileNotFoundError(
            f"Target ontology does not exist: "
            f"{target_path}"
        )

    return target_path


def apply_changes(
    ontology_file,
    add,
    remove,
):
    """Apply validated RDF additions and removals."""
    ontology_file = Path(ontology_file)

    graph = Graph()

    graph.parse(
        ontology_file,
        format="turtle",
    )

    # Validate everything BEFORE changing the ontology.
    validate_rdf_changes(
        add,
        "add",
    )

    validate_rdf_changes(
        remove,
        "remove",
    )

    remove_graph = parse_turtle_triples(
        remove
    )

    add_graph = parse_turtle_triples(
        add
    )

    removed = 0

    for triple in remove_graph:
        if triple in graph:
            graph.remove(triple)
            removed += 1

    added = 0

    for triple in add_graph:
        if triple not in graph:
            graph.add(triple)
            added += 1

    graph.serialize(
        destination=ontology_file,
        format="turtle",
    )

    return added, removed


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Apply generic SHACL LLM repair results "
            "to their target ontology files."
        )
    )

    parser.add_argument(
        "--requests",
        type=Path,
        required=True,
        help="SHACL batch request JSONL file.",
    )

    parser.add_argument(
        "--results",
        type=Path,
        required=True,
        help="LLM batch result JSONL file.",
    )

    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help=(
            "Repository root used to resolve target "
            "ontology files."
        ),
    )

    args = parser.parse_args()

    repo_root = args.repo_root.resolve()

    requests = load_jsonl(
        args.requests
    )

    results = load_jsonl(
        args.results
    )

    request_map = {
        request["custom_id"]: request
        for request in requests
    }

    applied = 0
    skipped = 0

    for result in results:
        custom_id = result.get(
            "custom_id"
        )

        if custom_id not in request_map:
            print(
                f"Skipping unknown request ID: "
                f"{custom_id}"
            )
            skipped += 1
            continue

        request = request_map[
            custom_id
        ]

        print(
            f"\n=== {custom_id} ==="
        )

        try:
            target_file = get_target_file(
                request,
                repo_root,
            )

            print(
                f"Target ontology: "
                f"{target_file}"
            )

            output_text = extract_output_text(
                result
            )

            repair = json.loads(
                output_text
            )

            add = repair.get(
                "add",
                [],
            )

            remove = repair.get(
                "remove",
                [],
            )

            explanation = repair.get(
                "explanation",
                "",
            )

            skip = repair.get(
                "skip",
                False,
            )

            print(
                f"Explanation: "
                f"{explanation}"
            )

            if skip:
                print(
                    "LLM requested skip=true"
                )
                skipped += 1
                continue

            added, removed = apply_changes(
                target_file,
                add,
                remove,
            )

            print(
                f"Triples added: {added}"
            )

            print(
                f"Triples removed: {removed}"
            )

            applied += 1

        except json.JSONDecodeError as exc:
            print(
                f"Invalid JSON returned by LLM: "
                f"{exc}"
            )
            skipped += 1

        except Exception as exc:
            print(
                f"Failed to apply repair: "
                f"{exc}"
            )
            skipped += 1

    print(
        "\n========================================"
    )

    print(
        f"Repairs applied: {applied}"
    )

    print(
        f"Repairs skipped: {skipped}"
    )

    print(
        "========================================"
    )


if __name__ == "__main__":
    main()
