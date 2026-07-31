import re
from dataclasses import dataclass, field
from pathlib import Path
from rdflib import Graph, URIRef

PREFIX_RE = re.compile(r'^@prefix\s+([A-Za-z0-9_-]*):\s*<([^>]*)>\s*\.\s*$', re.MULTILINE)
USED_PREFIX_RE = re.compile(r'(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_-]*)?:(?=[A-Za-z_])')

@dataclass
class TurtleFile:
    path: Path
    prefixes: dict = field(default_factory=dict)
    blocks: list = field(default_factory=list)

def parse_turtle_blocks(path: Path) -> TurtleFile:
    text = path.read_text(encoding="utf-8")

    prefixes: dict = {}
    for m in PREFIX_RE.finditer(text):
        prefixes[m.group(1)] = m.group(2)

    body = PREFIX_RE.sub("", text)
    blocks = [b.strip("\n") for b in re.split(r"\n\s*\n", body) if b.strip()]

    return TurtleFile(path=path, prefixes=prefixes, blocks=blocks)

def resolve_curie(curie: str, prefixes: dict) -> URIRef:
    prefix, _, local = curie.partition(":")
    if prefix not in prefixes:
        raise ValueError(f"Unknown '{prefix}'")
    return URIRef(prefixes[prefix] + local)

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
    tf = parse_turtle_blocks(source_path)
    input_term = resolve_curie(input_term, tf.prefixes)

    selected = [block for block in tf.blocks if block_contains_term(block, tf.prefixes, input_term)]

    return build_context_text(tf, selected)

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("input_term", help="CURIE, z.B. tbox:Entity")
    ap.add_argument("-o", "--output", type=Path, required=True)
    args = ap.parse_args()

    text = create_context_text(args.source, args.input_term)
    args.output.write_text(text, encoding="utf-8")
    print(f"Geschrieben: {args.output}")