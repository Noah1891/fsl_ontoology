#!/bin/bash

# 1. Check if both the pitfall number and lower term were provided
if [ -z "$1" ] || [ -z "$2" ]; then
    echo "Error: Missing arguments!"
    echo "Usage: $0 <PitfallNumber> <TermName>"
    echo "Example: $0 8 StructuredComputationConcept"
    exit 1
fi

PITFALL_NUM="$1"
TERM_NAME="$2"
FULL_LOWER_TERM="http://www.softlang.org/ontologies/ce#${TERM_NAME}"

# 2. Format the pitfall number to always have two digits (e.g., 8 -> P08, 12 -> P12)
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

# Path to the SPARQL query file (assumed to be named following your pattern)
SPARQL_QUERY="usages/find_${TERM_NAME}_usages.sparql"

echo "Starting extraction and usage query for: $TERM_NAME (Pitfall: $PITFALL_DIR)"

# 4. Create the target directory if it doesn't exist
if [ ! -d "$OUTPUT_DIR" ]; then
    echo "Directory does not exist. Creating: $OUTPUT_DIR"
    mkdir -p "$OUTPUT_DIR"
fi

# 5. Step 1: Run ROBOT extract for superclasses
echo "Extracting superclasses..."
robot extract --method MIREOT \
    --input ../merged/fsl/fsl_merged.ttl \
    --lower-term "$FULL_LOWER_TERM" \
    --output "$TEMP_SUPER"

if [ $? -ne 0 ]; then
    echo "Error during ROBOT extract. Aborting."
    exit 1
fi

# 6. Step 2: Run ROBOT query for usages (using your CONSTRUCT query)
echo "Querying usages..."
robot query --input ../merged/fsl/fsl_merged.ttl \
    --format ttl \
    --query "$SPARQL_QUERY" \
    "$TEMP_USAGES"

if [ $? -ne 0 ]; then
    echo "Error during ROBOT query. (Make sure $SPARQL_QUERY exists). Aborting."
    rm -f "$TEMP_SUPER"
    exit 1
fi

# 7. Step 3: Merge both fragments into a single ontology
echo "Merging superclasses and usages..."
robot merge --input "$TEMP_SUPER" \
            --input "$TEMP_USAGES" \
            --output "$FINAL_OUTPUT"

# Clean up all temporary files
rm -f "$TEMP_SUPER" "$TEMP_USAGES"

echo "Successfully completed! Combined file saved at: $FINAL_OUTPUT"