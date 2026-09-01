#!/usr/bin/env python3
"""Shared batch-request builder, branching by which experiment calls it.

Each experiment keeps its own domain logic (SAREF: one evidence record -> one
request; OnToology: pitfall-code-specific prompts/schemas over many affected
elements) -- this module only owns the dispatch and the parts that are
genuinely identical: JSON I/O, schema validation, and the '/v1/responses'
request envelope (common.openai_batch.build_responses_request).
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.jsonio import read_json  # noqa: E402
from common.openai_batch import build_responses_request  # noqa: E402
from common.schema_validation import validate_against_schema  # noqa: E402


def _build_saref_experiment_request(args: argparse.Namespace) -> None:
    evidence = read_json(args.evidence)
    evidence_schema = read_json(args.evidence.parent / "release-evidence.schema.json")
    errors = validate_against_schema(evidence, evidence_schema)
    if errors:
        raise ValueError(f"Input does not match release-evidence.schema.json: {'; '.join(errors)}")
    response_schema = read_json(args.schema)

    request = build_responses_request(
        custom_id=evidence["runId"],
        model=args.model,
        instructions=args.prompt.read_text(encoding="utf-8"),
        input_text=json.dumps(evidence, indent=2),
        schema=response_schema,
        schema_name="fsl_version_addition",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(request) + "\n", encoding="utf-8")
    print(f"Wrote request for {evidence['runId']} to {args.output}")


def _build_ontoology_request(args: argparse.Namespace) -> None:
    ontoology_scripts = REPO_ROOT / "ontoology" / "python_scripts"
    if str(ontoology_scripts) not in sys.path:
        sys.path.insert(0, str(ontoology_scripts))
    import build_batch_request as ontoology_builder  # teammate's own module, unmodified

    reqs = ontoology_builder.build_batch_requests(
        merged_ontology_path=args.merged_ontology,
        oops_xml_path=str(args.oops_xml),
        pitfall_ids=list(range(1, 42)),
        fsl_summary_path=args.fsl_summary,
    )
    ontoology_builder.write_batch_file(reqs, str(args.output))


def build_batch_request(context: str, args: argparse.Namespace) -> None:
    if context == "saref-experiment":
        _build_saref_experiment_request(args)
    elif context == "ontoology":
        _build_ontoology_request(args)
    else:
        raise ValueError(f"Unknown context: {context}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="context", required=True)

    saref = subparsers.add_parser("saref-experiment")
    saref.add_argument("--evidence", required=True, type=Path)
    saref.add_argument("--prompt", required=True, type=Path)
    saref.add_argument("--schema", required=True, type=Path)
    saref.add_argument("--output", required=True, type=Path)
    saref.add_argument("--model", default="MODEL_TO_BE_SELECTED")

    ontoology = subparsers.add_parser("ontoology")
    ontoology.add_argument("--merged-ontology", dest="merged_ontology", required=True, type=Path)
    ontoology.add_argument("--oops-xml", dest="oops_xml", required=True, type=Path)
    ontoology.add_argument("--fsl-summary", dest="fsl_summary", required=True, type=Path)
    ontoology.add_argument("--output", required=True, type=Path)

    args = parser.parse_args()
    build_batch_request(args.context, args)


if __name__ == "__main__":
    main()
