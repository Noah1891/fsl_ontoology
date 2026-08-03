import re
from dataclasses import dataclass, field
from pathlib import Path
from rdflib import Graph, BNode, URIRef
from rdflib.namespace import RDFS
import subprocess
import sys

PREFIX_RE = re.compile(r'^@prefix\s+([A-Za-z0-9_-]*):\s*<([^>]*)>\s*\.\s*$', re.MULTILINE)
USED_PREFIX_RE = re.compile(r'(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_-]*)?:(?=[A-Za-z_])')

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
    """Represents a parsed Turtle file."""
    path: Path
    prefixes: dict[str, str] = field(default_factory=dict)
    blocks: list[str] = field(default_factory=list)


def parse_turtle(path: Path) -> TurtleFile:
    """Reads a .ttl file and separates prefix declarations from the remaining statements."""
    text = path.read_text(encoding="utf-8")

    prefixes: dict[str, str] = {}
    for m in PREFIX_RE.finditer(text):
        prefixes[m.group(1)] = m.group(2)

    body = PREFIX_RE.sub("", text)
    blocks = [b.strip("\n") for b in re.split(r"\n\s*\n", body) if b.strip()]

    return TurtleFile(path=path, prefixes=prefixes, blocks=blocks)


def find_ontology_block(tf: TurtleFile) -> tuple[str, list[str]]:
    matches = [b for b in tf.blocks if re.search(r"\ba\s+owl:Ontology\b", b)]
    if len(matches) != 1:
        raise ValueError(
            f"{tf.path}: expected exactly one 'a owl:Ontology' block, found: {len(matches)}"
        )
    ontology_block = matches[0]
    rest = [b for b in tf.blocks if b is not ontology_block]
    return ontology_block, rest


def get_ontology_subject(ontology_block: str) -> str:
    """Extracts the subject IRI (e.g., <http://.../ae>) from the ontology block."""
    m = re.match(r"\s*<([^>]+)>", ontology_block)
    if not m:
        raise ValueError("Could not determine subject IRI of the ontology block:\n" + ontology_block)
    return m.group(1)


def module_prefix_name(module_path: Path) -> str:
    """Derives the prefix name to use from the filename (e.g., 'ae' from 'ae.ttl')."""
    return module_path.stem


def find_self_prefix(tf: TurtleFile, ontology_subject_iri: str) -> str | None:
    for prefix, iri in tf.prefixes.items():
        if iri.rstrip("#") == ontology_subject_iri.rstrip("#"):
            return prefix
    return None


def rewrite_default_prefix(text: str, old_prefix: str, new_prefix: str) -> str:
    if old_prefix:
        pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(old_prefix)}:(?=[A-Za-z_])")
    else:
        pattern = re.compile(r"(?<![A-Za-z0-9_:]):(?=[A-Za-z_])")
    return pattern.sub(f"{new_prefix}:", text)


def remove_import(ontology_block: str, iri_to_remove: str) -> str:
    m = re.search(r"owl:imports\s+(.*?)\s*\.\s*$", ontology_block, re.DOTALL)
    if not m:
        return ontology_block

    imports_str = m.group(1)
    iris = re.findall(r"<([^>]+)>", imports_str)
    if iri_to_remove not in iris:
        return ontology_block  # Nothing to remove

    remaining = [i for i in iris if i != iri_to_remove]

    prefix_part = ontology_block[: m.start()]
    suffix_part = ontology_block[m.end():]

    if remaining:
        first, *rest = remaining
        lines = [f"owl:imports <{first}>" + ("," if rest else " .")]
        for i, iri in enumerate(rest):
            is_last = i == len(rest) - 1
            lines.append(" " * 8 + f"<{iri}>" + (" ." if is_last else ","))
        new_imports_stmt = "\n".join(lines)
        return prefix_part + new_imports_stmt + suffix_part
    else:
        head = prefix_part.rstrip()
        if head.endswith(";"):
            head = head[:-1].rstrip() + " ."
        return head


def merge(main_path: Path, module_paths: list[Path]) -> str:
    main_tf = parse_turtle(main_path)
    ontology_block, main_rest_blocks = find_ontology_block(main_tf)

    merged_prefixes: dict[str, str] = dict(main_tf.prefixes)
    appended_module_blocks: list[str] = []

    for module_path in module_paths:
        mod_tf = parse_turtle(module_path)
        mod_ontology_block, mod_rest_blocks = find_ontology_block(mod_tf)
        mod_subject = get_ontology_subject(mod_ontology_block)

        ontology_block = remove_import(ontology_block, mod_subject)

        new_prefix = module_prefix_name(module_path)
        self_prefix = find_self_prefix(mod_tf, mod_subject)

        rewritten_blocks = []
        for block in mod_rest_blocks:
            new_block = block
            if self_prefix is not None:
                new_block = rewrite_default_prefix(new_block, self_prefix, new_prefix)
            rewritten_blocks.append(new_block)
        appended_module_blocks.extend(rewritten_blocks)

        for prefix, iri in mod_tf.prefixes.items():
            if prefix == self_prefix:
                continue
            if prefix in merged_prefixes:
                if merged_prefixes[prefix] != iri:
                    raise ValueError(
                        f"Prefix conflikt: '{prefix}:' in {main_path} points to "
                        f"<{merged_prefixes[prefix]}>, in {module_path} it points to <{iri}>"
                    )
                continue
            merged_prefixes[prefix] = iri

        if self_prefix is not None:
            merged_prefixes.pop(new_prefix, None)
            merged_prefixes[new_prefix] = mod_tf.prefixes[self_prefix]

    prefix_lines = [f"@prefix {p}: <{iri}> ." for p, iri in merged_prefixes.items()]
    all_blocks = [ontology_block] + main_rest_blocks + appended_module_blocks

    out = "\n".join(prefix_lines) + "\n\n" + "\n\n".join(all_blocks) + "\n"
    return out

def run_command(command: list) -> None:
    printable_command = " ".join(str(part) for part in command)
    print(f"Running: {printable_command}")

    result = subprocess.run(command, capture_output=True, text=True)

    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        print(f"Command failed with exit code {result.returncode}: {printable_command}", file=sys.stderr)
        sys.exit(result.returncode)

def convert_merged_to_owl(input: Path, output: Path) -> None:
    command = [
        "robot", "convert",
        "--input", str(input),
        "--format", "owl",
        "--output", str(output),
    ]
    run_command(command)

def block_subject_term(block_text: str, prefixes: dict):
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


def resolve_term(term, prefixes: dict):
    if isinstance(term, (URIRef, BNode)):
        return term
    if not isinstance(term, str):
        return None

    text = term.strip()
    if not text:
        return None

    if text.startswith("<") and text.endswith(">"):
        text = text[1:-1]

    if text.startswith(("http://", "https://")):
        return URIRef(text)

    prefix, _, local = text.partition(":")
    if prefix in prefixes and local:
        return URIRef(prefixes[prefix] + local)

    return None


def find_context_subjects(g: Graph, input_term) -> set:
    needed = set()
    for s, p, o in g:
        if input_term == s or input_term == p or input_term == o:
            needed.add(s)
    return {s for s in needed if isinstance(s, (URIRef, BNode))}


def find_superclass_chain(g: Graph, input_term) -> set:
    chain = set()
    frontier = {input_term}
    visited = {input_term}
    while frontier:
        next_frontier = set()
        for node in frontier:
            for parent in g.objects(node, RDFS.subClassOf):
                if parent not in visited:
                    chain.add(parent)
                    visited.add(parent)
                    next_frontier.add(parent)
        frontier = next_frontier
    return chain


def block_contains_term(block_text: str, prefixes: dict, term) -> bool:
    header = "\n".join(f"@prefix {p}: <{iri}> ." for p, iri in prefixes.items())
    g_block = Graph()
    try:
        g_block.parse(data=header + "\n\n" + block_text, format="turtle")
    except Exception:
        return False
    for s, p, o in g_block:
        if term == s or term == p or term == o:
            return True
    return False


def used_prefixes(blocks: list, all_prefixes: dict) -> dict:
    text = "\n".join(blocks)
    used_names = set()
    for m in USED_PREFIX_RE.finditer(text):
        prefix = m.group(1) or ""
        if prefix in all_prefixes:
            used_names.add(prefix)
    return {p: iri for p, iri in all_prefixes.items() if p in used_names}


def build_context_text(tf: TurtleFile, blocks: list) -> str:
    used = used_prefixes(blocks, tf.prefixes)
    prefix_lines = [f"@prefix {p}: <{iri}> ." for p, iri in used.items()]
    return "\n".join(prefix_lines) + "\n\n" + "\n\n".join(blocks) + "\n"


def create_context_text(source_path, input_term) -> str:
    source_path = Path(source_path)
    tf = parse_turtle(source_path)

    g = Graph()
    g.parse(source_path, format="turtle")

    resolved_input_term = resolve_term(input_term, tf.prefixes)
    if resolved_input_term is None:
        resolved_input_term = input_term

    subjects_needed = find_context_subjects(g, resolved_input_term)

    superclasses = find_superclass_chain(g, resolved_input_term)
    subjects_needed |= {s for s in superclasses if isinstance(s, (URIRef, BNode))}

    selected = []

    for block in tf.blocks:
        if block_contains_term(block, tf.prefixes, resolved_input_term):
            selected.append(block)
            continue

        subj = block_subject_term(block, tf.prefixes)
        if subj is not None and subj in subjects_needed:
            selected.append(block)

    selected_set = set(selected)
    ordered = [b for b in tf.blocks if b in selected_set]

    return build_context_text(tf, ordered)

