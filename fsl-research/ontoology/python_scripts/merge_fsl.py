import re
from pathlib import Path

from turtle_file import TurtleFile, parse_turtle


def find_ontology_block(tf: TurtleFile) -> tuple[str, list[str]]:
    """Extracts the 'owl:Ontology' block from the main FSL module and seperates it from the rest.
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
    """Extracts the URI given the 'owl:Ontology' block of a model.
    """
    m = re.match(r"\s*<([^>]+)>", ontology_block)
    if not m:
        raise ValueError("Could not determine subject IRI of the ontology block:\n" + ontology_block)
    return m.group(1)


def module_prefix_name(module_path: Path) -> str:
    """Derives the prefix name to use from the filename (e.g., 'ae' from 'ae.ttl').
    """
    return module_path.stem


def find_self_prefix(tf: TurtleFile, ontology_subject_uri: str) -> str | None:
    """Finds the prefix used for elements defined in the module itself
    """
    for prefix, uri in tf.prefixes.items():
        if uri.rstrip("#") == ontology_subject_uri.rstrip("#"):
            return prefix
    return None


def rewrite_default_prefix(text: str, old_prefix: str, new_prefix: str) -> str:
    """Replaces old prefix with new one in text block
    """
    if old_prefix:
        pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(old_prefix)}:(?=[A-Za-z_])")
    else:
        pattern = re.compile(r"(?<![A-Za-z0-9_:]):(?=[A-Za-z_])")
    return pattern.sub(f"{new_prefix}:", text)


def remove_import(ontology_block: str, uri_to_remove: str) -> str:
    """Removes a import triple including the given uri from a block
    """
    m = re.search(r"owl:imports\s+(.*?)\s*\.\s*$", ontology_block, re.DOTALL)
    if not m:
        return ontology_block

    imports_str = m.group(1)
    uris = re.findall(r"<([^>]+)>", imports_str)
    if uri_to_remove not in uris:
        return ontology_block  # Nothing to remove

    remaining = [i for i in uris if i != uri_to_remove]

    prefix_part = ontology_block[: m.start()]
    suffix_part = ontology_block[m.end():]

    if remaining:
        first, *rest = remaining
        lines = [f"owl:imports <{first}>" + ("," if rest else " .")]
        for i, uri in enumerate(rest):
            is_last = i == len(rest) - 1
            lines.append(" " * 8 + f"<{uri}>" + (" ." if is_last else ","))
        new_imports_stmt = "\n".join(lines)
        return prefix_part + new_imports_stmt + suffix_part
    else:
        head = prefix_part.rstrip()
        if head.endswith(";"):
            head = head[:-1].rstrip() + " ."
        return head


def merge(main_path: Path, module_paths: list[Path]) -> str:
    """Merges modules listed under their paths into main ontology module
    """
    # parses main FSL module and extracts 'owl:Ontology' block
    main_tf = parse_turtle(main_path)
    ontology_block, main_rest_blocks = find_ontology_block(main_tf)

    # initializes prefixes for merged file with main module prefixes
    merged_prefixes: dict[str, str] = dict(main_tf.prefixes)

    appended_module_blocks: list[str] = []

    for module_path in module_paths:
        # parse single module of FSL
        mod_tf = parse_turtle(module_path)
        # seperate 'owl:Ontology block from the rest
        mod_ontology_block, mod_rest_blocks = find_ontology_block(mod_tf)
        # extracts the module URI
        mod_subject = get_ontology_subject(mod_ontology_block)

        # remove import triple of merged module from main 'owl:Ontology' block
        ontology_block = remove_import(ontology_block, mod_subject)

        # creates a prefix name from the file name
        new_prefix = module_prefix_name(module_path)
        # identify prefix used in single module for itself
        self_prefix = find_self_prefix(mod_tf, mod_subject)

        rewritten_blocks = []
        for block in mod_rest_blocks:
            if self_prefix is not None:
                # replace prefix used in the single module by the one derived from its file name
                block = rewrite_default_prefix(block, self_prefix, new_prefix)
            rewritten_blocks.append(block)
        appended_module_blocks.extend(rewritten_blocks)

        # add new prefixes to merged prefixes
        for prefix, uri in mod_tf.prefixes.items():
            if prefix == self_prefix:
                continue
            if prefix in merged_prefixes:
                if merged_prefixes[prefix] != uri:
                    raise ValueError(
                        f"Prefix conflict: '{prefix}:' in {main_path} points to "
                        f"<{merged_prefixes[prefix]}>, in {module_path} it points to <{uri}>"
                    )
                continue
            merged_prefixes[prefix] = uri

        # add uri that was saved under the old self-prefix 
        if self_prefix is not None:
            merged_prefixes[new_prefix] = mod_tf.prefixes[self_prefix]

    prefix_lines = [f"@prefix {p}: <{uri}> ." for p, uri in merged_prefixes.items()]
    all_blocks = [ontology_block] + main_rest_blocks + appended_module_blocks

    out = "\n".join(prefix_lines) + "\n\n" + "\n\n".join(all_blocks) + "\n"
    return out