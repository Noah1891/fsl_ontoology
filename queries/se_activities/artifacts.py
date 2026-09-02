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

SELECT DISTINCT ?x
WHERE {

  # An assertion between a software artifact x and an SE activity a
  {
    ?a ?p ?x
  }
  UNION
  {
    ?x ?p ?a
  }

  # An artifact type
  {
    SELECT DISTINCT ?x
    WHERE {
      ?x rdfs:subClassOf+ tbox:ArtifactEntity .
    }
  }

  # An SE activity as a non-immediate subclass of tbox:EngineeringActivity
  { 
    SELECT DISTINCT ?a
    WHERE {
      ?a rdfs:subClassOf+ tbox:EngineeringActivity .
      FILTER NOT EXISTS {
        ?a rdfs:subClassOf tbox:EngineeringActivity .
      }
    }
  }

  # Counting only ontological properties
  FILTER(?p != rdf:type) 
  FILTER(?p != rdfs:subClassOf)
  FILTER(?p != rdfs:domain)
  FILTER(?p != rdfs:range)
  FILTER(?p != rdfs:label)
  FILTER(?p != rdfs:comment)
  FILTER(?p != foaf:isPrimaryTopicOf)
  FILTER(?p != foaf:page)
}
ORDER BY ?x
"""

# Reporting query result
for row in g.query(query):
    print(row['x'])
