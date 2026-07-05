import re
import sys
import platform
import subprocess
from pathlib import Path

# 1. Validate arguments
if len(sys.argv) < 3:
    print("Error: Missing arguments!")
    print(f"Usage: {sys.argv[0]} <PitfallNumber> <Prefix:TermName>")
    print(f"Example: {sys.argv[0]} 8 ce:DataConcept")
    sys.exit(1)

pitfall_num = sys.argv[1]
input_term = sys.argv[2]  # Expected format: "ce:DataConcept"

if ":" not in input_term:
    print(f"Error: Invalid input term '{input_term}'. Expected format 'prefix:TermName'")
    sys.exit(1)

prefix, term_name = input_term.split(":", 1)

# Dynamically determine the base URI based on the prefix
prefixes_map = {
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
}

if prefix not in prefixes_map:
    print(f"Error: Unknown prefix '{prefix}'!")
    sys.exit(1)

base_uri = prefixes_map[prefix]
full_lower_term = f"{base_uri}{term_name}"

# 2. Format the pitfall number (e.g., "8" -> "P08", "12" -> "P12")
if re.match(r"^[0-9]$", pitfall_num):
    pitfall_dir = f"P0{pitfall_num}"
else:
    pitfall_dir = f"P{pitfall_num}"

# 3. Define paths cross-platform using pathlib
base_path = Path(__file__).parent  # Directory where this script is located
output_dir = base_path / "../llm_prompting/pitfalls" / pitfall_dir / term_name
final_output = output_dir / f"context_{term_name}.ttl"
input_ttl = base_path / "../merged/fsl/fsl_merged.ttl"

# Temporary files in the current working directory
temp_super = Path(f"temp_super_{term_name}.ttl")
temp_usages = Path(f"temp_usages_{term_name}.ttl")
temp_sparql = Path(f"temp_query_{term_name}.sparql")
temp_merged = Path(f"temp_merged_{term_name}.ttl")

print(f"Starting extraction and usage query for: {input_term} (Pitfall: {pitfall_dir})")

# 4. Create the target directory if it does not exist
output_dir.mkdir(parents=True, exist_ok=True)

# --- Dynamic SPARQL generation ---
print(f"Generating temporary SPARQL query for {input_term}...")
sparql_content = f"""PREFIX owl: <http://www.w3.org/2002/07/owl#>
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

temp_sparql.write_text(sparql_content, encoding="utf-8")

# Helper function to clean up temporary files on error or completion
def cleanup():
    for f in [temp_super, temp_usages, temp_sparql, temp_merged]:
        if f.exists():
            f.unlink()

# Determine if the platform is Windows to handle shell-specific execution
is_windows = platform.system() == "Windows"

# 5. Step 1: Run ROBOT extract for superclasses
print("Extracting superclasses...")
cmd_extract = [
    "robot", "extract",
    "--method", "MIREOT",
    "--input", str(input_ttl),
    "--lower-term", full_lower_term,
    "--output", str(temp_super)
]

res = subprocess.run(cmd_extract, shell=is_windows)
if res.returncode != 0:
    print("Error during ROBOT extract. Aborting.")
    cleanup()
    sys.exit(1)

# 6. Step 2: Run ROBOT query for usages
print("Querying usages...")
cmd_query = [
    "robot", "query",
    "--input", str(input_ttl),
    "--format", "ttl",
    "--query", str(temp_sparql),
    str(temp_usages)
]

res = subprocess.run(cmd_query, shell=is_windows)
if res.returncode != 0:
    print("Error during ROBOT query. Aborting.")
    cleanup()
    sys.exit(1)

# 7. Step 3: Merge both fragments into a single ontology
print("Merging superclasses and usages...")
cmd_merge = [
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
    "--output", str(temp_merged)
]

res = subprocess.run(cmd_merge, shell=is_windows)
if res.returncode != 0:
    print("Error during ROBOT merge. Aborting.")
    cleanup()
    sys.exit(1)

# Convert to the final TTL format
print("Converting to final TTL format...")
cmd_convert = [
    "robot", "convert",
    "--input", str(temp_merged),
    "--format", "ttl",
    "--output", str(final_output)
]
subprocess.run(cmd_convert, shell=is_windows)

# Clean up all temporary files
cleanup()
print(f"Successfully completed! Combined file saved at: {final_output}")