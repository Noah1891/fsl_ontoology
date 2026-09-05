from pathlib import Path

from rdflib import Graph, Namespace
from pyshacl import validate

from build_shacl_batch_request import (
    build_shacl_comment_request,
    write_request,
)


# Project root
ROOT = Path(__file__).resolve().parents[3]

# Files used by the SHACL experiment
DATA_FILE = ROOT / "fsl-research/ontoology/demo/shacl_invalid.ttl"
SHAPE_FILE = ROOT / "validation/ClassDeclarationsMustHaveCommentShape.ttl"
ONTOLOGY_FILE = ROOT / "ontologies/tbox.ttl"

# Output JSONL batch request
OUTPUT_FILE = (
    ROOT
    / "fsl-research/ontoology/llm_prompting/batches/shacl_comment.jsonl"
)


def get_context(graph, focus_node):
    """Get Turtle statements about the affected element."""

    context_graph = Graph()

    for triple in graph.triples((focus_node, None, None)):
        context_graph.add(triple)

    return context_graph.serialize(format="turtle")


def main():

    # ---------------------------------------------------------
    # 1. Load the ontology/demo data
    # ---------------------------------------------------------

    data_graph = Graph()
    data_graph.parse(DATA_FILE, format="turtle")

    # ---------------------------------------------------------
    # 2. Run SHACL validation
    # ---------------------------------------------------------

    conforms, results_graph, results_text = validate(
        data_graph,
        shacl_graph=str(SHAPE_FILE),
        ont_graph=str(ONTOLOGY_FILE),
        inference="none",
    )

    print(results_text)

    # ---------------------------------------------------------
    # 3. Stop if there are no violations
    # ---------------------------------------------------------

    if conforms:
        print("No SHACL violations found.")
        return

    # ---------------------------------------------------------
    # 4. Extract SHACL validation result
    # ---------------------------------------------------------

    SH = Namespace("http://www.w3.org/ns/shacl#")

    validation_result = next(
        results_graph.subjects(
            predicate=SH.focusNode,
        )
    )

    focus_node = next(
        results_graph.objects(
            validation_result,
            SH.focusNode,
        )
    )

    message = next(
        results_graph.objects(
            validation_result,
            SH.resultMessage,
        )
    )

    # ---------------------------------------------------------
    # 5. Extract relevant Turtle context
    # ---------------------------------------------------------

    context_ttl = get_context(
        data_graph,
        focus_node,
    )

    # ---------------------------------------------------------
    # 6. Build LLM batch request
    # ---------------------------------------------------------

    request = build_shacl_comment_request(
        focus_node=str(focus_node),
        message=str(message),
        context_ttl=context_ttl,
        request_id="SHACL_COMMENT_001",
    )

    # ---------------------------------------------------------
    # 7. Write JSONL batch request
    # ---------------------------------------------------------

    write_request(
        request,
        OUTPUT_FILE,
    )

    # ---------------------------------------------------------
    # 8. Print information for the demo
    # ---------------------------------------------------------

    print(f"Focus node: {focus_node}")
    print(f"Message: {message}")
    print(f"Batch request: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
