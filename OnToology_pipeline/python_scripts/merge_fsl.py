"""
merge_fsl.py
==================

Merges Turtle modules (e.g. ae.ttl, ce.ttl, ...) into the main ontology
file (fsl.ttl), exactly following the pattern this pattern:

  * The "a owl:Ontology ; ..." blocks of the modules are discarded -- only
    the ontology header from the main file is kept.
  * The corresponding merged import is removed from the main file's
    owl:imports list.
  * A module's (usually empty) default prefix ":" is replaced with a
    "real" prefix (the module's file name, e.g. "ae").
  * All @prefix declarations are unioned; new prefixes from a module are
    appended, and the module's (renamed) own prefix ends up at the very
    end of the prefix list.
  * All remaining triples are appended to the main file unchanged (only
    with the rewritten default prefix).

The implementation deliberately works on the text level (rather than via
rdflib serialization) so that formatting, comments, and triple order are
preserved exactly.

Usage:
    python merge_fsl.py fsl.ttl ae.ttl ce.ttl ... -o fsl_merged.ttl

Assumptions (matching the softlang ontology):
    * Each top-level statement is separated from the others by an actual
      blank line (no statement itself contains a blank line).
    * Each module file contains exactly one "a owl:Ontology" block.
    * The prefix name for a module corresponds to its file name (without
      .ttl).
"""

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path


PREFIX_RE = re.compile(r'^@prefix\s+([A-Za-z0-9_-]*):\s*<([^>]*)>\s*\.\s*$', re.MULTILINE)


@dataclass
class TurtleFile:
    """Represents a parsed Turtle file."""
    path: Path
    prefixes: dict[str, str] = field(default_factory=dict)   # prefix -> IRI (order = order of occurrence)
    blocks: list[str] = field(default_factory=list)           # Top-level statements, in original order


def parse_turtle(path: Path) -> TurtleFile:
    """Reads a .ttl file and separates prefix declarations from the remaining statements."""
    text = path.read_text(encoding="utf-8")

    prefixes: dict[str, str] = {}
    for m in PREFIX_RE.finditer(text):
        prefixes[m.group(1)] = m.group(2)

    body = PREFIX_RE.sub("", text)
    # Split into blocks (top-level statements): separated by blank lines.
    blocks = [b.strip("\n") for b in re.split(r"\n\s*\n", body) if b.strip()]

    return TurtleFile(path=path, prefixes=prefixes, blocks=blocks)


def find_ontology_block(tf: TurtleFile) -> tuple[str, list[str]]:
    """Searches for the block containing 'a owl:Ontology'.

    Returns (ontology_block, remaining_blocks).
    Raises an error if not exactly one such block exists.
    """
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
    """Finds the prefix key in the module whose IRI matches the module's own ontology IRI
    (e.g., ':' -> 'http://www.softlang.org/ontologies/ae#' for subject
    'http://www.softlang.org/ontologies/ae'). Returns None if no matching prefix is found."""
    for prefix, iri in tf.prefixes.items():
        if iri.rstrip("#") == ontology_subject_iri.rstrip("#"):
            return prefix
    return None


def rewrite_default_prefix(text: str, old_prefix: str, new_prefix: str) -> str:
    """Replaces occurrences of 'old_prefix:Name' with 'new_prefix:Name'.

    When old_prefix == '' (default prefix ':'), a lookbehind is used
    to ensure that only *isolated* ':Foo' tokens are replaced
    (not, for example, the 'x' in 'tbox:Foo' or URIs like 'http://...').
    """
    if old_prefix:
        pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(old_prefix)}:(?=[A-Za-z_])")
    else:
        # Default prefix ':' : must not be directly preceded by a word character or
        # another ':' (which would indicate a different prefix like 'tbox:').
        pattern = re.compile(r"(?<![A-Za-z0-9_:]):(?=[A-Za-z_])")
    return pattern.sub(f"{new_prefix}:", text)


def remove_import(ontology_block: str, iri_to_remove: str) -> str:
    """Removes an IRI from the owl:imports list of the ontology block and
    reformats the list (indentation matching the original: first entry
    directly after 'owl:imports ', subsequent entries indented by 8 spaces).
    If the list is empty afterwards, the entire owl:imports line including
    the preceding ';' is removed."""

    m = re.search(r"owl:imports\s+(.*?)\s*\.\s*$", ontology_block, re.DOTALL)
    if not m:
        # No owl:imports present -> nothing to do.
        return ontology_block

    imports_str = m.group(1)
    iris = re.findall(r"<([^>]+)>", imports_str)
    if iri_to_remove not in iris:
        return ontology_block  # Nothing to remove

    remaining = [i for i in iris if i != iri_to_remove]

    prefix_part = ontology_block[: m.start()]
    suffix_part = ontology_block[m.end():]  # Usually "" (block ends with '.')

    if remaining:
        first, *rest = remaining
        lines = [f"owl:imports <{first}>" + ("," if rest else " .")]
        for i, iri in enumerate(rest):
            is_last = i == len(rest) - 1
            lines.append(" " * 8 + f"<{iri}>" + (" ." if is_last else ","))
        new_imports_stmt = "\n".join(lines)
        return prefix_part + new_imports_stmt + suffix_part
    else:
        # List is empty -> remove owl:imports completely.
        # prefix_part ends with '\n    ' (indentation) right before 'owl:imports'.
        # The preceding statement must then end with '.' instead of ';'.
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

        # 1) Remove import from the main file.
        ontology_block = remove_import(ontology_block, mod_subject)

        # 2) Determine the module's own prefix and rename it.
        new_prefix = module_prefix_name(module_path)
        self_prefix = find_self_prefix(mod_tf, mod_subject)  # Usually ''

        rewritten_blocks = []
        for block in mod_rest_blocks:
            new_block = block
            if self_prefix is not None:
                new_block = rewrite_default_prefix(new_block, self_prefix, new_prefix)
            rewritten_blocks.append(new_block)
        appended_module_blocks.extend(rewritten_blocks)

        # 3) Merge prefixes: append new (previously unknown) prefixes of the module,
        #    place the module's own prefix at the very end.
        for prefix, iri in mod_tf.prefixes.items():
            if prefix == self_prefix:
                continue  # Handled separately at the end
            if prefix in merged_prefixes:
                if merged_prefixes[prefix] != iri:
                    raise ValueError(
                        f"Prefix conflikt: '{prefix}:' in {main_path} points to "
                        f"<{merged_prefixes[prefix]}>, in {module_path} it points to <{iri}>"
                    )
                continue
            merged_prefixes[prefix] = iri

        if self_prefix is not None:
            # Remove any previously existing entry under the new name and re-insert at the end
            merged_prefixes.pop(new_prefix, None)
            merged_prefixes[new_prefix] = mod_tf.prefixes[self_prefix]

    # Assemble the final file
    prefix_lines = [f"@prefix {p}: <{iri}> ." for p, iri in merged_prefixes.items()]
    all_blocks = [ontology_block] + main_rest_blocks + appended_module_blocks

    out = "\n".join(prefix_lines) + "\n\n" + "\n\n".join(all_blocks) + "\n"
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Merges turtle-ontology modules into main module.")
    ap.add_argument("main_file", type=Path, help="Main module, e.g. fsl.ttl")
    ap.add_argument("modules", type=Path, nargs="+", help="Modules to merge, e.g. ae.ttl ce.ttl ...")
    ap.add_argument("-o", "--output", type=Path, required=True, help="output file")
    args = ap.parse_args()

    result = merge(args.main_file, args.modules)
    args.output.write_text(result, encoding="utf-8")
    print(f"Written: {args.output}")


if __name__ == "__main__":
    main()