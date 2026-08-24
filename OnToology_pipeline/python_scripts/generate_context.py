from pathlib import Path
from rdflib import Graph, URIRef, BNode
from rdflib.namespace import RDFS, OWL, RDF
import re
import networkx as nx

from turtle_file import TurtleFile, parse_turtle, select_blocks_by_subjects, block_contains_term, used_prefixes


OWL_PROPERTY_TYPES = {
    OWL.ObjectProperty,
    OWL.DatatypeProperty,
    OWL.AnnotationProperty,
    OWL.FunctionalProperty,
    OWL.InverseFunctionalProperty,
    OWL.TransitiveProperty,
    OWL.SymmetricProperty,
    OWL.AsymmetricProperty,
    OWL.ReflexiveProperty,
    OWL.IrreflexiveProperty,
}


def determine_property_types(g: Graph, input_term) -> set:
    return {
        obj
        for _, _, obj in g.triples((input_term, RDF.type, None))
        if obj in OWL_PROPERTY_TYPES
    }


def find_usage_subjects(tf: TurtleFile, term) -> set:
    """Collects all subjects of triples that include a given term."""
    subjects = set()
    subjects.update(s for s, _, _ in tf.graph.triples((None, None, term)))
    subjects.update(s for s, _, _ in tf.graph.triples((None, term, None)))
    subjects.update(s for s, _, _ in tf.graph.triples((term, None, None)))
    return {s for s in subjects if isinstance(s, (URIRef, BNode))}


def find_superclass_chain(tf: TurtleFile, term) -> set:
    """Collects all terms that are part of a superclass chain
    starting from the given term."""
    frontier = [term]
    visited = {term}
    while frontier:
        current = frontier.pop()
        for parent in tf.graph.objects(current, RDFS.subClassOf):
            if parent not in visited:
                visited.add(parent)
                frontier.append(parent)
    return visited


def find_subclass_chain(g: Graph, input_term) -> set:
    frontier = [input_term]
    visited = {input_term}

    while frontier:
        current = frontier.pop()
        for child in g.subjects(RDFS.subClassOf, current):
            if child not in visited:
                visited.add(child)
                frontier.append(child)

    return visited


def block_has_type_declaration(tf: TurtleFile, block: str, type_uri) -> bool:
    triples = tf.block_triples.get(block, [])
    inverse_subjects = {s for s, p, _ in triples if p == OWL.inverseOf}
    return any(
        p == RDF.type and o == type_uri and s not in inverse_subjects
        for s, p, o in triples
    )


def select_all_type_blocks(tf: TurtleFile, type_uri) -> set:
    """Select all blocks that define a class of the given type."""
    return {
        block for block in tf.blocks
        if block_has_type_declaration(tf, block, type_uri)
    }


def select_property_blocks(tf: TurtleFile, term) -> set:
    """Select all blocks that define a property type.
    """
    prop_types = determine_property_types(tf.graph, term)
    if not prop_types:
        return set()

    selected = set()
    for prop_type in prop_types:
        selected |= select_all_type_blocks(tf, prop_type)

    return selected


def select_usage_blocks(tf: TurtleFile, term) -> set:
    """Selects blocks that include triples with the given term."""
    usage_subjects = find_usage_subjects(tf, term)
    return select_blocks_by_subjects(tf, usage_subjects)


def select_superclass_blocks(tf: TurtleFile, term) -> set:
    """Selects all blocks in a 'rdf:subClassOf' hierarchy above
    the given term."""
    superclass_subjects = find_superclass_chain(tf, term)
    return select_blocks_by_subjects(tf, superclass_subjects)


def select_subclass_blocks(tf: TurtleFile, g: Graph, input_term) -> set:
    subclass_subjects = find_subclass_chain(g, input_term)
    return select_blocks_by_subjects(tf, subclass_subjects)


def build_usage_graph(tf: TurtleFile, affected_elements: set):
    """Builds directed subgraph of all the affected elements for applying networksx graph algorithms.
    """
    dg = nx.DiGraph()
    dg.add_nodes_from(affected_elements)

    for elem in affected_elements:
        for block, subj in tf.block_subject.items():
            if subj != elem:
                continue
            for _, _, obj in tf.block_triples[block]:
                if obj in affected_elements and obj != elem:
                    dg.add_edge(elem, obj)
    return dg


def find_incoming_usage_subjects(tf: TurtleFile, scc_members: set) -> set:
    """Finds all subjects that reference a member of any SCC except those
    who are part of the SCC themselves.
    """
    subjects = set()
    for term in scc_members:
        for s, _, _ in tf.graph.triples((None, None, term)):
            if isinstance(s, (URIRef, BNode)) and s not in scc_members:
                subjects.add(s)
    return subjects


def build_context_text(tf: TurtleFile, blocks: list) -> str:
    used = used_prefixes(blocks, tf.prefixes)
    prefix_lines = [f"@prefix {p}: <{iri}> ." for p, iri in used.items()]
    return "\n".join(prefix_lines) + "\n\n" + "\n\n".join(blocks) + "\n"


def create_contexts(source_path: Path, affected_elements, pitfall_code):
    tf = parse_turtle(source_path)
    contexts = []
    aes = []
    match pitfall_code:
        case 'P04':
            for ae in affected_elements:
                selected = select_usage_blocks(tf, ae)
                ordered  = [b for b in tf.blocks if b in selected]
                contexts.append(build_context_text(tf, ordered))
                aes.append({ae})
        case 'P07':
            dg = build_usage_graph(tf, affected_elements)
            sccs = list(nx.strongly_connected_components(dg))
            for scc in sccs:
                selected = select_blocks_by_subjects(tf, scc)
                neighbor_subjects = find_incoming_usage_subjects(tf, scc)
                selected |= select_blocks_by_subjects(tf, neighbor_subjects)
                ordered  = [b for b in tf.blocks if b in selected]
                contexts.append(build_context_text(tf, ordered))
                aes.append(scc)
        case "P08":
            for ae in affected_elements:
                selected = select_usage_blocks(tf, ae)
                selected |= select_superclass_blocks(tf, ae)
                ordered  = [b for b in tf.blocks if b in selected]
                contexts.append(build_context_text(tf, ordered))
                aes.append({ae})
        case "P13":
            for ae in affected_elements:
                selected = select_property_blocks(tf, ae)
                ordered  = [b for b in tf.blocks if b in selected]
                contexts.append(build_context_text(tf, ordered))
                aes.append({ae})
    return contexts, aes