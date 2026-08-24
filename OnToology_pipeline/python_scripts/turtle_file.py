import re
from dataclasses import dataclass, field
from pathlib import Path
from rdflib import Graph, BNode, URIRef, Literal
from rdflib.namespace import RDF

# RegEx to parse prefix definitions in Turtle file
PREFIX_RE = re.compile(r'^@prefix\s+([A-Za-z0-9_-]*):\s*<([^>]*)>\s*\.\s*$', re.MULTILINE)

USED_PREFIX_RE = re.compile(r'(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_-]*)?:(?=[A-Za-z_])')

# Matches the subject token at the start of a block: <IRI>, prefix:local, or _:bnode
SUBJECT_TOKEN_RE = re.compile(
    r'^\s*(?:'
    r'<(?P<iri>[^>]*)>'
    r'|(?P<bnode>_:[A-Za-z0-9_]+)(?=[\s;,.]|$)'
    r'|(?P<prefix>[A-Za-z][A-Za-z0-9_-]*)?:'
    r'(?P<local>(?:[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)?)'
    r'(?=[\s;,.]|$)'
    r')'
)


@dataclass
class TurtleFile:
    """Represents a Turtle file: its path, prefix definitions, raw text blocks,
    the fully parsed rdflib Graph, and a pre-computed mapping from each block
    to its own subject term and the triples that actually belong to it.
    """
    path: Path
    prefixes: dict[str, str] = field(default_factory=dict)
    blocks: list[str] = field(default_factory=list)
    graph: Graph = field(default_factory=Graph)
    block_subject: dict[str, object] = field(default_factory=dict)
    block_triples: dict[str, list] = field(default_factory=dict)


def block_subject_term(block_text: str, prefixes: dict):
    """Extracts the subject term (URIRef or BNode) from the start of a block,
    using only regex -- no rdflib parsing. Returns None if the block doesn't
    start with a recognizable subject token (e.g. anonymous '[ ... ]' blocks).
    """
    m = SUBJECT_TOKEN_RE.match(block_text)
    if not m:
        return None
    if m.group("iri") is not None:
        return URIRef(m.group("iri"))
    if m.group("bnode") is not None:
        return BNode(m.group("bnode")[2:])
    prefix = m.group("prefix") or ""
    local = m.group("local") or ""
    if prefix not in prefixes:
        return None
    return URIRef(prefixes[prefix] + local)


def _term_candidates(term, prefixes: dict[str, str]) -> list[str]:
    """Possible textual representations of a term as it could appear in
    Turtle syntax, used for verifying that a triple actually belongs to a
    given block's text (not just to its subject).
    """
    if isinstance(term, URIRef):
        iri = str(term)
        candidates = [f"<{iri}>"]
        for prefix, ns in prefixes.items():
            if iri.startswith(ns):
                candidates.append(f"{prefix}:{iri[len(ns):]}")
        return candidates
    if isinstance(term, Literal):
        # Lexical value is used as a heuristic; quoting/language tags vary,
        # so only the core string content is checked.
        return [str(term)]
    if isinstance(term, BNode):
        # Blank nodes have no stable, recognizable textual form (e.g. "[ ... ]"),
        # so no reliable match is possible.
        return []
    return [str(term)]


def _triple_in_block_text(predicate, obj, block_text: str, prefixes: dict[str, str]) -> bool:
    """Verifies that a (predicate, object) pair actually occurs in the raw
    text of a block, to avoid falsely attributing triples that merely share
    the same subject but live in a different block elsewhere in the file.
    """
    pred_candidates = [" a "] if predicate == RDF.type else _term_candidates(predicate, prefixes)
    if not any(c in block_text for c in pred_candidates):
        return False

    obj_candidates = _term_candidates(obj, prefixes)
    if not obj_candidates:
        return False
    return any(c in block_text for c in obj_candidates)


def _collect_block_triples(g: Graph, subj, block_text: str, prefixes: dict) -> list:
    """Collects all triples belonging to a block: direct triples of `subj`
    that verifiably occur in the block's text (substring-checked, to guard
    against the same subject appearing in a different block elsewhere),
    plus -- unconditionally -- all triples reachable transitively via
    blank-node objects. Blank nodes have no independent textual form and
    are locally scoped to the triple that introduces them, so once a
    blank node has been reached via a verified triple, all of its own
    triples safely belong to the same block (nested owl:Restriction
    blocks, RDF collections via rdf:first/rdf:rest, etc.).
    """
    collected = []
    for s, p, o in g.triples((subj, None, None)):
        if isinstance(o, BNode):
            pred_candidates = [" a "] if p == RDF.type else _term_candidates(p, prefixes)
            if any(c in block_text for c in pred_candidates):
                collected.append((s, p, o))
        elif _triple_in_block_text(p, o, block_text, prefixes):
            collected.append((s, p, o))

    frontier = [o for _, _, o in collected if isinstance(o, BNode)]
    seen = set(frontier)
    while frontier:
        current = frontier.pop()
        for s, p, o in g.triples((current, None, None)):
            collected.append((s, p, o))
            if isinstance(o, BNode) and o not in seen:
                seen.add(o)
                frontier.append(o)

    return collected


def _parse_block_fallback(block_text: str, header: str):
    """Fallback for blocks whose subject can't be determined by regex alone
    (e.g. anonymous '[ ... ]' blocks, RDF collections as subject). Parses
    just this block individually via rdflib -- only used for the rare
    blocks the fast path can't handle.
    """
    g_block = Graph()
    try:
        g_block.parse(data=header + "\n\n" + block_text, format="turtle")
    except Exception:
        return None, []
    triples = list(g_block)
    subjects = {s for s, _, _ in triples}
    subject = next(iter(subjects), None)
    return subject, triples


def parse_turtle(path: Path) -> TurtleFile:
    """Takes path to Turtle file and converts it to TurtleFile datatype.
    Parses the file exactly once with rdflib; per-block subjects and
    triples are then derived from that single parse (plus a rare fallback
    for blocks without a regex-recognizable subject), so no repeated
    per-block rdflib parsing is needed downstream.
    """
    text = path.read_text(encoding="utf-8")

    prefixes: dict[str, str] = {}
    for m in PREFIX_RE.finditer(text):
        prefixes[m.group(1)] = m.group(2)

    body = PREFIX_RE.sub("", text)
    blocks = [b.strip("\n") for b in re.split(r"\n\s*\n", body) if b.strip()]

    g = Graph()
    g.parse(path, format="turtle")  # single full parse of the file

    header = "\n".join(f"@prefix {p}: <{iri}> ." for p, iri in prefixes.items())

    block_subject: dict[str, object] = {}
    block_triples: dict[str, list] = {}

    for block in blocks:
        subj = block_subject_term(block, prefixes)
        if subj is not None:
            block_subject[block] = subj
            block_triples[block] = _collect_block_triples(g, subj, block, prefixes)
        else:
            fallback_subj, fallback_triples = _parse_block_fallback(block, header)
            block_subject[block] = fallback_subj
            block_triples[block] = fallback_triples

    return TurtleFile(
        path=path,
        prefixes=prefixes,
        blocks=blocks,
        graph=g,
        block_subject=block_subject,
        block_triples=block_triples,
    )


def select_blocks_by_subjects(tf: TurtleFile, subjects: set) -> set:
    """Returns all blocks that contain at least one triple whose subject is
    in the given set -- checks all triples attributed to the block
    (including nested blank-node structures collected transitively), not
    just the block's own top-level subject.
    """
    selected = set()
    for block, triples in tf.block_triples.items():
        if any(s in subjects for s, _, _ in triples):
            selected.add(block)
    return selected


def block_contains_term(tf: TurtleFile, block: str, term) -> bool:
    """Checks whether a term occurs anywhere (s, p, or o) among the
    pre-computed triples of a block -- no re-parsing needed."""
    return any(term in (s, p, o) for s, p, o in tf.block_triples.get(block, []))


def used_prefixes(blocks: list, all_prefixes: dict) -> dict:
    text = "\n".join(blocks)
    used_names = set()
    for m in USED_PREFIX_RE.finditer(text):
        prefix = m.group(1) or ""
        if prefix in all_prefixes:
            used_names.add(prefix)
    return {p: iri for p, iri in all_prefixes.items() if p in used_names}


if __name__ == '__main__':
    SCRIPT_DIR = Path(__file__).parent.resolve()
    source_path = SCRIPT_DIR / "dummy.ttl"
    parse_turtle(source_path)
