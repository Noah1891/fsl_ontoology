import argparse
import json
import re
from pathlib import Path

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDFS


def load_request(request_file: Path, custom_id: str) -> str:
    for line in request_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue

        entry = json.loads(line)

        if entry.get("custom_id") != custom_id:
            continue

        input_text = entry["body"]["input"]

        match = re.search(
            r"Affected element:\s*\n([^\n]+)",
            input_text,
        )

        if not match:
            raise RuntimeError(
                f"Could not extract affected element for {custom_id}."
            )

        return match.group(1).strip()

    raise RuntimeError(f"No matching request found for {custom_id}.")


def load_result(result_file: Path) -> tuple[str, str]:
    for line in result_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue

        entry = json.loads(line)

        if entry.get("error"):
            raise RuntimeError(
                f"LLM batch request failed: {entry['error']}"
            )

        custom_id = entry["custom_id"]
        body = (entry.get("response") or {}).get("body") or {}

        for item in body.get("output", []):
            if item.get("type") != "message":
                continue

            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    result = json.loads(content["text"])
                    comment = result.get("comment")

                    if not comment:
                        raise RuntimeError(
                            "LLM result does not contain a non-empty comment."
                        )

                    return custom_id, comment

    raise RuntimeError("No usable structured LLM result found.")


def apply_fix(
    request_file: Path,
    result_file: Path,
    ontology_file: Path,
) -> None:
    custom_id, comment = load_result(result_file)

    focus_node = load_request(request_file, custom_id)

    graph = Graph()
    graph.parse(ontology_file, format="turtle")

    graph.add(
        (
            URIRef(focus_node),
            RDFS.comment,
            Literal(comment, lang="en"),
        )
    )

    graph.serialize(
        destination=ontology_file,
        format="turtle",
    )

    print(f"Applied rdfs:comment to {focus_node}")
    print(f"Updated: {ontology_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--requests",
        required=True,
        type=Path,
    )

    parser.add_argument(
        "--results",
        required=True,
        type=Path,
    )

    parser.add_argument(
        "--ontology",
        required=True,
        type=Path,
    )

    args = parser.parse_args()

    apply_fix(
        args.requests,
        args.results,
        args.ontology,
    )
