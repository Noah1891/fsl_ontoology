#!/bin/bash

# 1. Check if both the pitfall number and lower term were provided
if [ -z "$1" ] || [ -z "$2" ]; then
    echo "Error: Missing arguments!"
    echo "Usage: $0 <PitfallNumber> <Prefix:TermName>"
    echo "Example: $0 8 ce:DataConcept"
    echo "Example: $0 8 le:LegalConcept"
    exit 1
fi

PITFALL_NUM="$1"
INPUT_TERM="$2" # Erwartet jetzt "prefix:ClassName", z. B. "ce:DataConcept"

# Trenne Präfix und Klassenname (z. B. "ce:DataConcept" -> "ce" und "DataConcept")
PREFIX="${INPUT_TERM%%:*}"
TERM_NAME="${INPUT_TERM#*:}"

# Dynamische Ermittlung der Basis-URI basierend auf dem übergebenen Präfix
case "$PREFIX" in
    tbox|te|ce|ae|pe|le|ie|fe)
        BASE_URI="http://www.softlang.org/ontologies/${PREFIX}#"
        ;;
    owl)
        BASE_URI="http://www.w3.org/2002/07/owl#"
        ;;
    rdfs)
        BASE_URI="http://www.w3.org/2000/01/rdf-schema#"
        ;;
    *)
        echo "Error: Unknown prefix '$PREFIX'!"
        exit 1
        ;;
esac

FULL_LOWER_TERM="${BASE_URI}${TERM_NAME}"

# 2. Format the pitfall number to always have two digits
if [[ "$PITFALL_NUM" =~ ^[0-9]$ ]]; then
    PITFALL_DIR="P0$PITFALL_NUM"
else
    PITFALL_DIR="P$PITFALL_NUM"
fi

# 3. Dynamically define paths based on the pitfall directory
OUTPUT_DIR="../llm_prompting/pitfalls/${PITFALL_DIR}/${TERM_NAME}"
FINAL_OUTPUT="${OUTPUT_DIR}/context_${TERM_NAME}.ttl"

# Temporary files for the intermediate steps
TEMP_SUPER="temp_super_${TERM_NAME}.ttl"
TEMP_USAGES="temp_usages_${TERM_NAME}.ttl"
TEMP_SPARQL="temp_query_${TERM_NAME}.sparql"

echo "Starting extraction and usage query for: $INPUT_TERM (Pitfall: $PITFALL_DIR)"

# 4. Create the target directory if it doesn't exist
if [ ! -d "$OUTPUT_DIR" ]; then
    echo "Directory does not exist. Creating: $OUTPUT_DIR"
    mkdir -p "$OUTPUT_DIR"
fi

# --- Dynamische SPARQL-Generierung (Nutzt jetzt das variable $INPUT_TERM) ---
echo "Generating temporary SPARQL query for $INPUT_TERM..."
cat << EOF > "$TEMP_SPARQL"
PREFIX owl: <http://www.w3.org/2002/07/owl#>
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

CONSTRUCT {
  ?subject ?property ${INPUT_TERM} .
  ${INPUT_TERM} ?property ?object .

  ?subject ?subjectProperty ?subjectValue .
  ?subjectValue ?subBlankProp ?subBlankVal .

  ?object ?objectProperty ?objectValue .
  ?objectValue ?objBlankProp ?objBlankVal .

  ?objectProperty rdf:type ?objPropType .
  ?subjectProperty rdf:type ?subPropType .
  ?objBlankVal rdf:type ?objBlankValType .
  ?subBlankVal rdf:type ?subBlankValType .
}
WHERE {
  {
    ?subject ?property ${INPUT_TERM} .
    ?subject ?subjectProperty ?subjectValue .
    OPTIONAL { ?subjectProperty rdf:type ?subPropType . }
    OPTIONAL {
      FILTER(isBlank(?subjectValue))
      ?subjectValue ?subBlankProp ?subBlankVal .
      OPTIONAL { ?subBlankVal rdf:type ?subBlankValType . }
    }
  }
  UNION
  {
    ${INPUT_TERM} ?property ?object .
    ?object ?objectProperty ?objectValue .
    OPTIONAL { ?objectProperty rdf:type ?objPropType . }
    OPTIONAL {
      FILTER(isBlank(?objectValue))
      ?objectValue ?objBlankProp ?objBlankVal .
      OPTIONAL { ?objBlankVal rdf:type ?objBlankValType . }
    }
  }
}
EOF
# ----------------------------------------------------------------------------

# 5. Step 1: Run ROBOT extract for superclasses
echo "Extracting superclasses..."
robot extract --method MIREOT \
    --input ../merged/fsl/fsl_merged.ttl \
    --lower-term "$FULL_LOWER_TERM" \
    --output "$TEMP_SUPER"

if [ $? -ne 0 ]; then
    echo "Error during ROBOT extract. Aborting."
    rm -f "$TEMP_SPARQL"
    exit 1
fi

# 6. Step 2: Run ROBOT query for usages
echo "Querying usages..."
robot query --input ../merged/fsl/fsl_merged.ttl \
    --format ttl \
    --query "$TEMP_SPARQL" \
    "$TEMP_USAGES"

if [ $? -ne 0 ]; then
    echo "Error during ROBOT query. Aborting."
    rm -f "$TEMP_SUPER" "$TEMP_SPARQL"
    exit 1
fi

# 7. Step 3: Merge both fragments into a single ontology
echo "Merging superclasses and usages..."
robot merge --input "$TEMP_SUPER" \
            --input "$TEMP_USAGES" \
            --add-prefix "rdf: http://www.w3.org/1999/02/22-rdf-syntax-ns#" \
            --add-prefix "foaf: http://xmlns.com/foaf/0.1/" \
            --add-prefix "xsd: http://www.w3.org/2001/XMLSchema#" \
            --add-prefix "tbox: http://www.softlang.org/ontologies/tbox#" \
            --add-prefix "te: http://www.softlang.org/ontologies/te#" \
            --add-prefix "ce: http://www.softlang.org/ontologies/ce#" \
            --add-prefix "ae: http://www.softlang.org/ontologies/ae#" \
            --add-prefix "owl: http://www.w3.org/2002/07/owl#" \
            --add-prefix "pe: http://www.softlang.org/ontologies/pe#" \
            --add-prefix "le: http://www.softlang.org/ontologies/le#" \
            --add-prefix "rdfs: http://www.w3.org/2000/01/rdf-schema#" \
            --add-prefix "ie: http://www.softlang.org/ontologies/ie#" \
            --add-prefix "fe: http://www.softlang.org/ontologies/fe#" \
            --output "$FINAL_OUTPUT"

# Clean up all temporary files
rm -f "$TEMP_SUPER" "$TEMP_USAGES" "$TEMP_SPARQL"

echo "Successfully completed! Combined file saved at: $FINAL_OUTPUT"