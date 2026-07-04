#!/bin/bash

# Prüfen, ob eine Eingabedatei übergeben wurde
if [ -z "$1" ]; then
    echo "Fehler: Bitte gib eine Eingabedatei an!"
    echo "Nutzung: $0 <deine_datei.ttl>"
    exit 1
fi

INPUT_FILE="$1"
OUTPUT_FILE="classhierarchy_data_concept_super.ttl"

# Der eigentliche Ersetzungs- und Schreibvorgang
(
echo '@prefix tbox: <http://www.softlang.org/ontologies/tbox#> .'
echo '@prefix ae: <http://www.softlang.org/ontologies/ae#> .'
echo '@prefix ce: <http://www.softlang.org/ontologies/ce#> .'
echo '@prefix fe: <http://www.softlang.org/ontologies/fe#> .'
echo '@prefix ie: <http://www.softlang.org/ontologies/ie#> .'
echo '@prefix le: <http://www.softlang.org/ontologies/le#> .'
echo '@prefix pe: <http://www.softlang.org/ontologies/pe#> .'
echo '@prefix te: <http://www.softlang.org/ontologies/te#> .'
sed -E \
  -e 's|<http://www.softlang.org/ontologies/ae#([^>]+)>|ae:\1|g' \
  -e 's|<http://www.softlang.org/ontologies/ce#([^>]+)>|ce:\1|g' \
  -e 's|<http://www.softlang.org/ontologies/fe#([^>]+)>|fe:\1|g' \
  -e 's|<http://www.softlang.org/ontologies/ie#([^>]+)>|ie:\1|g' \
  -e 's|<http://www.softlang.org/ontologies/le#([^>]+)>|le:\1|g' \
  -e 's|<http://www.softlang.org/ontologies/pe#([^>]+)>|pe:\1|g' \
  -e 's|<http://www.softlang.org/ontologies/te#([^>]+)>|te:\1|g' \
  -e 's|<http://www.softlang.org/ontologies/tbox#([^>]+)>|tbox:\1|g' \
  "$INPUT_FILE"
) > "$OUTPUT_FILE"

echo "Fertig! Die bereinigte Datei wurde als '$OUTPUT_FILE' gespeichert."