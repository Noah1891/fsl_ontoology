from pathlib import Path
from rdflib import Graph

# Parse all Turtle files of the ontology
ttl_dir = Path("../../ontologies")
ttl_files = sorted(ttl_dir.glob("*.ttl"))
g = Graph()
for ttl in ttl_files:
    g.parse(ttl, format="turtle")

# Query of interest
query = """
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX tbox: <http://www.softlang.org/ontologies/tbox#>
PREFIX fsl: <http://www.softlang.org/ontologies/>

SELECT DISTINCT ?l
WHERE {

  # An assertiom between software language and concept
  {
    ?c ?p ?l
  }
  UNION
  {
    ?l ?p ?c
  }

  # An actual language
  {
    SELECT DISTINCT ?l 
    WHERE {
      ?lsc rdfs:subClassOf+ tbox:LanguageEntity .
      ?l rdf:type ?lsc .
    }
  }

  # A concept entity
  { 
    SELECT DISTINCT ?c
    WHERE {
      {
        ?csc rdfs:subClassOf+ tbox:LanguageConcept .
        ?c rdf:type ?csc .
      }
      UNION
      {
        ?c rdfs:subClassOf+ tbox:LanguageConcept .
      }
    }
  }
  FILTER(STRSTARTS(STR(?p), STR(fsl:)))
}
ORDER BY ?l
"""

# Reporting query result
for row in g.query(query):
    print(row['l'])
