"""
Runs a sequence of ROBOT commands to:
1. Merge the fsl ontology using a catalog file.
2. Convert several ontologies into their "punned" versions.
3. Convert the merged fsl ontology into OWL format.
"""

import subprocess
import sys
from pathlib import Path

# Directory in which this script is located (OS-independent anchor point)
SCRIPT_DIR = Path(__file__).resolve().parent

# Base paths, relative to the script location
ONTOLOGIES_DIR = SCRIPT_DIR / ".." / ".." / "ontologies"
PUNNED_DIR = ONTOLOGIES_DIR / "punned"
MERGED_DIR = SCRIPT_DIR / ".." / "merged" / "fsl"

CATALOG_FILE = ONTOLOGIES_DIR / "catalog-v001.xml"
FSL_INPUT_FILE = ONTOLOGIES_DIR / "fsl.ttl"
FSL_MERGED_TTL = MERGED_DIR / "fsl_merged.ttl"
FSL_MERGED_OWL = MERGED_DIR / "fsl_merged.owl"

ONTOLOGY_NAMES = ["ae", "ce", "fe", "ie", "le", "pe", "tbox", "te", "fsl"]

ROBOT_COMMAND = "robot"


def run_command(command: list) -> None:
    """Runs a shell command and stops the script if it fails."""
    printable_command = " ".join(str(part) for part in command)
    print(f"Running: {printable_command}")

    result = subprocess.run(command, capture_output=True, text=True)

    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        print(f"Command failed with exit code {result.returncode}: {printable_command}", file=sys.stderr)
        sys.exit(result.returncode)


def merge_fsl_ontology() -> None:
    """Merges the fsl ontology using the catalog file."""
    MERGED_DIR.mkdir(parents=True, exist_ok=True)

    command = [
        ROBOT_COMMAND, "merge",
        "--catalog", str(CATALOG_FILE),
        "--input", str(FSL_INPUT_FILE),
        "--output", str(FSL_MERGED_TTL),
    ]
    run_command(command)


def convert_to_punned(name: str) -> None:
    """Converts a single ontology into its punned version."""
    PUNNED_DIR.mkdir(parents=True, exist_ok=True)

    input_file = ONTOLOGIES_DIR / f"{name}.ttl"
    output_file = PUNNED_DIR / f"{name}_punned.ttl"

    command = [
        ROBOT_COMMAND, "convert",
        "--input", str(input_file),
        "--output", str(output_file),
    ]
    run_command(command)


def convert_merged_to_owl() -> None:
    """Converts the merged fsl ontology from TTL to OWL format."""
    command = [
        ROBOT_COMMAND, "convert",
        "--input", str(FSL_MERGED_TTL),
        "--format", "owl",
        "--output", str(FSL_MERGED_OWL),
    ]
    run_command(command)


def main():
    merge_fsl_ontology()

    for name in ONTOLOGY_NAMES:
        convert_to_punned(name)

    convert_merged_to_owl()

    print("All ROBOT commands completed successfully.")


if __name__ == "__main__":
    main()