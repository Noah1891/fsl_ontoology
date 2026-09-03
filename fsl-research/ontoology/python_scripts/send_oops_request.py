import argparse
import sys
import time
from pathlib import Path

import requests

OOPS_URL = "https://oops.linkeddata.es/rest"
REQUEST_TIMEOUT = (10, 120)  # (connect, read) seconds
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 30

# Directory in which this script is located (OS-independent)
SCRIPT_DIR = Path(__file__).resolve().parent

# Default output path, relative to the script location
DEFAULT_OUTPUT_PATH = SCRIPT_DIR / ".." / "oops_prompting" / "report" / "oops_report.xml"


def build_request_xml(ontology_content: str, ontology_uri: str = "", pitfalls: str = "") -> str:
    """Returns XML request string for OOPS API.
    """
    request_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
                      <OOPSRequest>
                      <OntologyURI>{ontology_uri}</OntologyURI>
                      <OntologyContent><![CDATA[{ontology_content}]]></OntologyContent>
                      <Pitfalls>{pitfalls}</Pitfalls>
                      <OutputFormat>XML</OutputFormat>
                      </OOPSRequest>"""
    return request_xml


def scan_ontology(owl_path: Path, output_path: Path = "oops_report.xml") -> str:
    ontology_content = owl_path.read_text(encoding="utf-8")

    body = build_request_xml(ontology_content)

    headers = {"Content-Type": "application/xml"}

    last_error: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            response = requests.post(OOPS_URL, data=body.encode("utf-8"), headers=headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            break
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_error = exc
            print(f"OOPS! request failed (attempt {attempt}/{RETRY_ATTEMPTS}): {exc}")
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    else:
        raise RuntimeError(f"OOPS! request failed after {RETRY_ATTEMPTS} attempts") from last_error

    output_path.write_text(response.text, encoding="utf-8")
    print(f"Report saved to: {output_path}")

    return response.text