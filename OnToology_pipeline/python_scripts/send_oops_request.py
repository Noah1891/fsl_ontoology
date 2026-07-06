"""
Sends an OWL/RDF-XML ontology to the OOPS! (OntOlogy Pitfall Scanner!) service
and saves the returned pitfall report as an XML file.
"""

import argparse
import sys
from pathlib import Path

import requests

OOPS_URL = "https://oops.linkeddata.es/rest"

# Directory in which this script is located (OS-independent)
SCRIPT_DIR = Path(__file__).resolve().parent

# Default output path, relative to the script location
DEFAULT_OUTPUT_PATH = SCRIPT_DIR / ".." / "oops_prompting" / "report" / "oops_report.xml"


def build_request_xml(ontology_content: str, ontology_uri: str = "", pitfalls: str = "") -> str:
    """Builds the XML request body for the OOPS! API."""
    # If the ontology content itself contains "]]>", the CDATA section
    # would be closed prematurely, so it needs to be split/escaped.
    safe_content = ontology_content.replace("]]>", "]]]]><![CDATA[>")

    request_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<OOPSRequest>
<OntologyURI>{ontology_uri}</OntologyURI>
<OntologyContent><![CDATA[{safe_content}]]></OntologyContent>
<Pitfalls>{pitfalls}</Pitfalls>
<OutputFormat>XML</OutputFormat>
</OOPSRequest>"""
    return request_xml


def scan_ontology(owl_path: str, output_path: str = "oops_report.xml") -> str:
    """Reads the .owl file, sends it to OOPS!, and saves the report."""
    ontology_content = Path(owl_path).read_text(encoding="utf-8")

    body = build_request_xml(ontology_content)

    headers = {"Content-Type": "application/xml"}

    response = requests.post(OOPS_URL, data=body.encode("utf-8"), headers=headers)
    response.raise_for_status()

    Path(output_path).write_text(response.text, encoding="utf-8")
    print(f"Report saved to: {output_path}")

    return response.text


def main():
    parser = argparse.ArgumentParser(
        description="Sends an OWL ontology to the OOPS! service and saves the report."
    )
    parser.add_argument("owl_file", help="Path to the .owl file (RDF/XML)")
    parser.add_argument(
        "-o", "--output", default=DEFAULT_OUTPUT_PATH,
        help=f"Path to the output file for the report (default: {DEFAULT_OUTPUT_PATH})"
    )
    args = parser.parse_args()

    try:
        scan_ontology(args.owl_file, args.output)
    except requests.exceptions.RequestException as e:
        print(f"Error while requesting OOPS!: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()