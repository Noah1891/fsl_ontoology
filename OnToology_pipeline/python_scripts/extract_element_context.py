"""
Extracts superclass information and usage statements for a given ontology
term, merges them, and writes the result as a TTL "context" file.

The core functionality is exposed as the function `generate_context()`,
which can be imported and called from other Python code. A minimal CLI
wrapper is kept at the bottom of the file for convenience.
"""

import re
import sys
import platform
import subprocess
from pathlib import Path

# Prefix -> base URI mapping used to resolve "prefix:TermName" input terms
PREFIXES_MAP = {
    "tbox": "http://www.softlang.org/ontologies/tbox#",
    "te": "http://www.softlang.org/ontologies/te#",
    "ce": "http://www.softlang.org/ontologies/ce#",
    "ae": "http://www.softlang.org/ontologies/ae#",
    "pe": "http://www.softlang.org/ontologies/pe#",
    "le": "http://www.softlang.org/ontologies/le#",
    "ie": "http://www.softlang.org/ontologies/ie#",
    "fe": "http://www.softlang.org/ontologies/fe#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
}

# Directory in which this script is located (OS-independent anchor point)
BASE_PATH = Path(__file__).resolve().parent

# Determine if the platform is Windows to handle shell-specific execution
IS_WINDOWS = platform.system() == "Windows"


def _build_sparql_query(input_term: str) -> str:
    """Builds the CONSTRUCT query used to find usages of the given term."""
    return f"""PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX tbox: <http://www.softlang.org/ontologies/tbox#>
PREFIX ae: <http://www.softlang.org/ontologies/ae#>
PREFIX ce: <http://www.softlang.org/ontologies/ce#>
PREFIX fe: <http://www.softlang.org/ontologies/fe#>
PREFIX ie: <http://www.softlang.org/ontologies/ie#>
PREFIX le: <http://www.softlang.org/ontologies/le#>
PREFIX pe: <http://www.softlang.org/ontologies/pe#>
PREFIX te: <http://www.softlang.org/ontologies/te#>

CONSTRUCT {{
  ?subject ?property {input_term} .
  {input_term} ?property ?object .

  ?subject ?subjectProperty ?subjectValue .
  ?subjectValue ?subBlankProp ?subBlankVal .

  ?object ?objectProperty ?objectValue .
  ?objectValue ?objBlankProp ?objBlankVal .

  ?objectProperty rdf:type ?objPropType .
  ?subjectProperty rdf:type ?subPropType .
  ?objBlankVal rdf:type ?objBlankValType .
  ?subBlankVal rdf:type ?subBlankValType .
}}
WHERE {{
  {{
    ?subject ?property {input_term} .
    ?subject ?subjectProperty ?subjectValue .
    OPTIONAL {{ ?subjectProperty rdf:type ?subPropType . }}
    OPTIONAL {{
      FILTER(isBlank(?subjectValue))
      ?subjectValue ?subBlankProp ?subBlankVal .
      OPTIONAL {{ ?subBlankVal rdf:type ?subBlankValType . }}
    }}
  }}
  UNION
  {{
    {input_term} ?property ?object .
    ?object ?objectProperty ?objectValue .
    OPTIONAL {{ ?objectProperty rdf:type ?objPropType . }}
    OPTIONAL {{
      FILTER(isBlank(?objectValue))
      ?objectValue ?objBlankProp ?objBlankVal .
      OPTIONAL {{ ?objBlankVal rdf:type ?objBlankValType . }}
    }}
  }}
}}
"""


def _cleanup(*files: Path) -> None:
    """Removes temporary files if they exist."""
    for f in files:
        if f.exists():
            f.unlink()


def run_robot(command: list, error_message: str, cleanup_files: tuple) -> None:
    """Runs a ROBOT command, cleaning up and exiting on failure."""
    result = subprocess.run(command, shell=IS_WINDOWS)
    if result.returncode != 0:
        print(error_message)
        _cleanup(*cleanup_files)
        raise RuntimeError(error_message)


def generate_context(pitfall_num: str, input_term: str) -> Path:
    """
    Generates a TTL "context" file for a given ontology term and pitfall.

    This bundles superclass information (via ROBOT MIREOT extraction) and
    all usages of the term (via a SPARQL CONSTRUCT query) into a single
    merged TTL file.

    Args:
        pitfall_num: The pitfall number, e.g. "8" or "12" (will be
            formatted as "P08" / "P12").
        input_term: The term to look up, in "prefix:TermName" format,
            e.g. "ce:DataConcept".

    Returns:
        The path to the generated context TTL file.

    Raises:
        ValueError: If the input term or prefix is invalid.
        RuntimeError: If one of the underlying ROBOT commands fails.
    """
    # 1. Validate and parse the input term
    if ":" not in input_term:
        raise ValueError(
            f"Invalid input term '{input_term}'. Expected format 'prefix:TermName'"
        )

    prefix, term_name = input_term.split(":", 1)

    if prefix not in PREFIXES_MAP:
        raise ValueError(f"Unknown prefix '{prefix}'!")

    base_uri = PREFIXES_MAP[prefix]
    full_lower_term = f"{base_uri}{term_name}"

    # 2. Format the pitfall number (e.g., "8" -> "P08", "12" -> "P12")
    if re.match(r"^[0-9]$", pitfall_num):
        pitfall_dir = f"P0{pitfall_num}"
    else:
        pitfall_dir = f"P{pitfall_num}"

    # 3. Define paths cross-platform using pathlib
    output_dir = BASE_PATH / "../llm_prompting/pitfalls" / pitfall_dir / term_name
    final_output = output_dir / f"context_{term_name}.ttl"
    input_ttl = BASE_PATH / "../merged/fsl/fsl_merged.ttl"

    # Temporary files in the current working directory
    temp_super = Path(f"temp_super_{term_name}.ttl")
    temp_usages = Path(f"temp_usages_{term_name}.ttl")
    temp_sparql = Path(f"temp_query_{term_name}.sparql")
    temp_merged = Path(f"temp_merged_{term_name}.ttl")
    temp_files = (temp_super, temp_usages, temp_sparql, temp_merged)

    print(f"Starting extraction and usage query for: {input_term} (Pitfall: {pitfall_dir})")

    # 4. Create the target directory if it does not exist
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Dynamic SPARQL generation ---
    print(f"Generating temporary SPARQL query for {input_term}...")
    temp_sparql.write_text(_build_sparql_query(input_term), encoding="utf-8")

    try:
        # 5. Step 1: Run ROBOT extract for superclasses
        print("Extracting superclasses...")
        run_robot(
            [
                "robot", "extract",
                "--method", "MIREOT",
                "--input", str(input_ttl),
                "--lower-term", full_lower_term,
                "--output", str(temp_super),
            ],
            "Error during ROBOT extract. Aborting.",
            temp_files,
        )

        # 6. Step 2: Run ROBOT query for usages
        print("Querying usages...")
        run_robot(
            [
                "robot", "query",
                "--input", str(input_ttl),
                "--format", "ttl",
                "--query", str(temp_sparql),
                str(temp_usages),
            ],
            "Error during ROBOT query. Aborting.",
            temp_files,
        )

        # 7. Step 3: Merge both fragments into a single ontology
        print("Merging superclasses and usages...")
        run_robot(
            [
                "robot", "merge",
                "--input", str(temp_super),
                "--input", str(temp_usages),
                "--add-prefix", "rdf: http://www.w3.org/1999/02/22-rdf-syntax-ns#",
                "--add-prefix", "foaf: http://xmlns.com/foaf/0.1/",
                "--add-prefix", "xsd: http://www.w3.org/2001/XMLSchema#",
                "--add-prefix", "tbox: http://www.softlang.org/ontologies/tbox#",
                "--add-prefix", "te: http://www.softlang.org/ontologies/te#",
                "--add-prefix", "ce: http://www.softlang.org/ontologies/ce#",
                "--add-prefix", "ae: http://www.softlang.org/ontologies/ae#",
                "--add-prefix", "owl: http://www.w3.org/2002/07/owl#",
                "--add-prefix", "pe: http://www.softlang.org/ontologies/pe#",
                "--add-prefix", "le: http://www.softlang.org/ontologies/le#",
                "--add-prefix", "rdfs: http://www.w3.org/2000/01/rdf-schema#",
                "--add-prefix", "ie: http://www.softlang.org/ontologies/ie#",
                "--add-prefix", "fe: http://www.softlang.org/ontologies/fe#",
                "--output", str(temp_merged),
            ],
            "Error during ROBOT merge. Aborting.",
            temp_files,
        )

        # Convert to the final TTL format
        print("Converting to final TTL format...")
        run_robot(
            [
                "robot", "convert",
                "--input", str(temp_merged),
                "--format", "ttl",
                "--output", str(final_output),
            ],
            "Error during ROBOT convert. Aborting.",
            temp_files,
        )
    finally:
        # Clean up all temporary files, whether the run succeeded or failed
        _cleanup(*temp_files)

    print(f"Successfully completed! Combined file saved at: {final_output}")
    return final_output


def _main() -> None:
    """Minimal CLI wrapper around generate_context()."""
    if len(sys.argv) < 3:
        print("Error: Missing arguments!")
        print(f"Usage: {sys.argv[0]} <PitfallNumber> <Prefix:TermName>")
        print(f"Example: {sys.argv[0]} 8 ce:DataConcept")
        sys.exit(1)

    pitfall_num = sys.argv[1]
    input_term = sys.argv[2]

    try:
        generate_context(pitfall_num, input_term)
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    _main()