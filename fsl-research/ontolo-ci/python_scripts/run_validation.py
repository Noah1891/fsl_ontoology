import argparse
from pathlib import Path

from rdflib import Graph, URIRef
from pyshacl import validate

from build_shacl_batch_request import build_batch_requests


SCRIPT_DIR = Path(__file__).resolve().parent
ONTOLO_CI_DIR = SCRIPT_DIR.parent
RESEARCH_DIR = ONTOLO_CI_DIR.parent
REPO_ROOT = RESEARCH_DIR.parent

VALIDATION_DIR = REPO_ROOT / "validation"
ONTOLOGIES_DIR = REPO_ROOT / "ontologies"

TBOX_SHAPES = [
    "ClassDeclarationsMustHaveCommentShape.ttl",
    "ClassDeclarationsMustHaveFoafLinkShape.ttl",
    "PropertyDeclarationsMustHaveCommentShape.ttl",
    "PropertyDeclarationsMustHaveFoafLinkShape.ttl",
    "MetamodelingShapes.ttl",
]

ABOX_SHAPES = [
    "TechnologySpacesMustBeSpecifiedShape.ttl",
    "TechnologySpacesMustHaveConformsToShape.ttl",
    "EngineeringActivitiesMustBeSpecifiedShape.ttl",
    "MethodologicalApproachesMustBeSpecifiedShape.ttl",
]

ABOX_ONTOLOGIES = [
    "ae.ttl",
    "ce.ttl",
    "fe.ttl",
    "ie.ttl",
    "le.ttl",
    "pe.ttl",
    "te.ttl",
]


def normalize_focus_node(value):
    if value is None:
        return ""

    value = str(value).strip()

    if value.startswith("<") and value.endswith(">"):
        return value[1:-1]

    return value


def read_graph(path):
    graph = Graph()
    graph.parse(path, format="turtle")
    return graph


def extract_violations(report_graph):
    violations = []

    SH = URIRef("http://www.w3.org/ns/shacl#")

    result_type = SH + "ValidationResult"
    focus_node_predicate = SH + "focusNode"
    message_predicate = SH + "resultMessage"
    source_shape_predicate = SH + "sourceShape"

    for result in report_graph.subjects(predicate=None, object=result_type):
        focus_nodes = list(
            report_graph.objects(
                result,
                focus_node_predicate,
            )
        )

        messages = list(
            report_graph.objects(
                result,
                message_predicate,
            )
        )

        source_shapes = list(
            report_graph.objects(
                result,
                source_shape_predicate,
            )
        )

        violations.append(
            {
                "focus_node": (
                    normalize_focus_node(focus_nodes[0])
                    if focus_nodes
                    else ""
                ),
                "message": (
                    str(messages[0])
                    if messages
                    else ""
                ),
                "source_shape": (
                    str(source_shapes[0])
                    if source_shapes
                    else ""
                ),
            }
        )

    return violations


def validate_tbox(data_file, shape_file):
    data_graph = read_graph(data_file)
    shape_graph = read_graph(shape_file)

    conforms, report_graph, _ = validate(
        data_graph,
        shacl_graph=shape_graph,
        inference="none",
        advanced=True,
    )

    return conforms, report_graph


def validate_abox(shape_file):
    master = read_graph(ONTOLOGIES_DIR / "tbox.ttl")

    for filename in ABOX_ONTOLOGIES:
        graph = read_graph(ONTOLOGIES_DIR / filename)

        for triple in graph:
            master.add(triple)

    shape_graph = read_graph(shape_file)

    conforms, report_graph, _ = validate(
        master,
        shacl_graph=shape_graph,
        inference="owlrl",
        advanced=True,
    )

    return conforms, report_graph


def repository_relative_path(path):
    path = Path(path).resolve()

    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return path.name


def find_source_file_for_focus_node(focus_node):
    focus_uri = URIRef(focus_node)

    candidates = [
        ONTOLOGIES_DIR / filename
        for filename in [
            "tbox.ttl",
            "ae.ttl",
            "ce.ttl",
            "fe.ttl",
            "ie.ttl",
            "le.ttl",
            "pe.ttl",
            "te.ttl",
        ]
    ]

    for candidate in candidates:
        if not candidate.is_file():
            continue

        try:
            graph = read_graph(candidate)

            if (
                focus_uri in graph.subjects()
                or focus_uri in graph.objects()
            ):
                return candidate
        except Exception:
            continue

    return None


def make_violation_records(
    violations,
    shape_path,
    data_file,
):
    records = []

    shape_reference = repository_relative_path(shape_path)

    data_reference = repository_relative_path(data_file)

    # Keep the actual filesystem path separately.
    absolute_data_file = str(Path(data_file).resolve())

    for violation in violations:
        records.append(
            {
                "focus_node": violation["focus_node"],
                "message": violation["message"],
                "shape": shape_reference,
                "data_file": data_reference,
                "data_file_path": absolute_data_file,
            }
        )

    return records


def resolve_abox_files():
    return [
        ONTOLOGIES_DIR / filename
        for filename in ABOX_ONTOLOGIES
    ]


def main():
    parser = argparse.ArgumentParser(
        description="Run FSL SHACL validation and generate LLM repair requests."
    )

    parser.add_argument(
        "--tbox",
        type=str,
        help="Optional TBox ontology file to validate instead of ontologies/tbox.ttl.",
    )

    parser.add_argument(
        "--abox",
        type=str,
        help="Optional ABox ontology file. If omitted, normal repository ABox validation is used.",
    )

    args = parser.parse_args()

    all_violation_records = []

    # ------------------------------------------------------------
    # TBOX
    # ------------------------------------------------------------

    if args.tbox:
        tbox_file = Path(args.tbox).expanduser().resolve()
        tbox_shapes = TBOX_SHAPES

        for shape_name in tbox_shapes:
            shape_path = VALIDATION_DIR / shape_name

            print(
                f"\n=== TBox: {shape_name} ==="
            )

            conforms, report_graph = validate_tbox(
                tbox_file,
                shape_path,
            )

            print(
                "\nValidation Report"
            )

            print(
                f"\nConforms: {conforms}\n"
            )

            violations = extract_violations(
                report_graph
            )

            if violations:
                print(
                    f"Results ({len(violations)}):"
                )

                for violation in violations:
                    print(
                        "\n\tConstraint Violation"
                    )
                    print(
                        f"\tFocus Node: {violation['focus_node']}"
                    )
                    print(
                        f"\tMessage: {violation['message']}"
                    )

                all_violation_records.extend(
                    make_violation_records(
                        violations,
                        shape_path,
                        tbox_file,
                    )
                )

    else:
        tbox_file = ONTOLOGIES_DIR / "tbox.ttl"

        for shape_name in TBOX_SHAPES:
            shape_path = VALIDATION_DIR / shape_name

            print(
                f"\n=== TBox: {shape_name} ==="
            )

            conforms, report_graph = validate_tbox(
                tbox_file,
                shape_path,
            )

            print(
                "\nValidation Report"
            )

            print(
                f"\nConforms: {conforms}\n"
            )

            violations = extract_violations(
                report_graph
            )

            if violations:
                print(
                    f"Results ({len(violations)}):"
                )

                for violation in violations:
                    print(
                        f"\n\tFocus Node: {violation['focus_node']}"
                    )
                    print(
                        f"\tMessage: {violation['message']}"
                    )

                all_violation_records.extend(
                    make_violation_records(
                        violations,
                        shape_path,
                        tbox_file,
                    )
                )

    # ------------------------------------------------------------
    # ABOX
    # ------------------------------------------------------------

    if args.abox:
        abox_file = Path(args.abox).expanduser().resolve()

        for shape_name in ABOX_SHAPES:
            shape_path = VALIDATION_DIR / shape_name

            print(
                f"\n=== ABox: {shape_name} ==="
            )

            conforms, report_graph = validate_abox(
                shape_path
            )

            print(
                "\nValidation Report"
            )

            print(
                f"\nConforms: {conforms}\n"
            )

            violations = extract_violations(
                report_graph
            )

            if violations:
                print(
                    f"Results ({len(violations)}):"
                )

                all_violation_records.extend(
                    make_violation_records(
                        violations,
                        shape_path,
                        abox_file,
                    )
                )

    else:
        for shape_name in ABOX_SHAPES:
            shape_path = VALIDATION_DIR / shape_name

            print(
                f"\n=== ABox: {shape_name} ==="
            )

            conforms, report_graph = validate_abox(
                shape_path
            )

            print(
                "\nValidation Report"
            )

            print(
                f"\nConforms: {conforms}\n"
            )

            violations = extract_violations(
                report_graph
            )

            if violations:
                print(
                    f"Results ({len(violations)}):"
                )

                for violation in violations:
                    print(
                        f"\n\tFocus Node: {violation['focus_node']}"
                    )
                    print(
                        f"\tMessage: {violation['message']}"
                    )

                source_file = find_source_file_for_focus_node(
                    violations[0]["focus_node"]
                )

                if source_file is not None:
                    all_violation_records.extend(
                        make_violation_records(
                            violations,
                            shape_path,
                            source_file,
                        )
                    )

    # ------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------

    print(
        "\n========================================"
    )

    print(
        f"\nTotal SHACL violations: "
        f"{len(all_violation_records)}"
    )

    print(
        "\n========================================"
    )

    for index, violation in enumerate(
        all_violation_records,
        start=1,
    ):
        print(
            f"\nViolation {index}:"
        )

        print(
            f"\n  Focus node: "
            f"{violation['focus_node']}"
        )

        print(
            f"\n  Message: "
            f"{violation['message']}"
        )

        print(
            f"\n  Shape: "
            f"{violation['shape']}"
        )

        print(
            f"\n  Data file: "
            f"{violation['data_file']}"
        )

        print(
            f"\n  Source path: "
            f"{violation['data_file_path']}"
        )

    output_path = (
        ONTOLO_CI_DIR
        / "llm_prompting"
        / "batches"
        / "shacl_requests.jsonl"
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if all_violation_records:
        build_batch_requests(
            all_violation_records,
            output_path,
        )

        print(
            f"\nWritten "
            f"{len(all_violation_records)} "
            f"SHACL batch requests to "
            f"{output_path}"
        )

        print(
            f"\nGenerated "
            f"{len(all_violation_records)} "
            f"LLM batch requests."
        )

        print(
            f"\nBatch file: "
            f"{output_path}"
        )

    else:
        print(
            "\nNo SHACL violations found."
        )


if __name__ == "__main__":
    main()
