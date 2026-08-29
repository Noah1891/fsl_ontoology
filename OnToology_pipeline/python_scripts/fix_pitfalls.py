"""
fix_pitfalls.py
================

Applies LLM-suggested fixes for OOPS! pitfalls detected in the "Foundations
of Software Languages" (FSL) ontology.

The pipeline works as follows:

1. All ontology modules (fsl, tbox, ae, ce, fe, ie, le, pe, te) are parsed
   once via `parse_turtle` (see the TurtleFile utility module you already
   have -- adjust the import below to match its actual filename).
2. A batch of LLM *requests* (JSONL, one object per line, in the
   OpenAI-batch "/v1/responses" request format you posted) and a batch of
   LLM *results* (JSONL, matching response objects) are loaded and joined
   on `custom_id`.
3. Each result's `custom_id` starts with the pitfall id (e.g. "P04_000_...").
   That id selects a `PitfallFixer` from a small registry.
4. The fixer receives the resolved "Affected Elements" (as rdflib URIRefs)
   plus the parsed JSON answer from the LLM, and decides what to change in
   the in-memory `TurtleFile` objects.
5. Any module that was actually touched gets serialized back to disk.

Only Pitfall P04 ("Creating unconnected ontology elements") is implemented
for now. To support another pitfall, just add another `PitfallFixer`
subclass and decorate it with `@register_fixer("P<nn>")` -- nothing else
in the script needs to change.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

from rdflib import Graph, URIRef

# --------------------------------------------------------------------------
# Adjust this import to match the actual module/filename that contains the
# TurtleFile dataclass and helper functions you already have.
# --------------------------------------------------------------------------
from turtle_file import (
    TurtleFile,
    parse_turtle,
    block_contains_term,
    used_prefixes,
    PREFIX_RE,
    USED_PREFIX_RE,
    SUBJECT_TOKEN_RE,
)

MODULE_NAMES = ["fsl", "tbox", "ae", "ce", "fe", "ie", "le", "pe", "te"]

# Namespaces for prefixes that fixers may need to introduce into a module
# that doesn't already declare them (e.g. when a newly generated block uses
# foaf:page but the module previously never referenced foaf). Keep this in
# sync with the ontology's actual namespaces.
KNOWN_PREFIXES = {
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "tbox": "http://www.softlang.org/ontologies/tbox#",
    "ae": "http://www.softlang.org/ontologies/ae#",
    "ce": "http://www.softlang.org/ontologies/ce#",
    "fe": "http://www.softlang.org/ontologies/fe#",
    "ie": "http://www.softlang.org/ontologies/ie#",
    "le": "http://www.softlang.org/ontologies/le#",
    "pe": "http://www.softlang.org/ontologies/pe#",
    "te": "http://www.softlang.org/ontologies/te#",
}

AFFECTED_RE = re.compile(r"Affected Elements:\s*(\[[^\]]*\])")
TURTLE_BLOCK_RE = re.compile(r"```turtle\s*(.*?)```", re.DOTALL)

# All default paths are resolved relative to this script's own location, not
# to the current working directory -- so the script behaves the same no
# matter where it is invoked from.
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ONTOLOGY_DIR = SCRIPT_DIR / "../../ontologies"
DEFAULT_BATCHES_DIR = SCRIPT_DIR / "../llm_prompting/batches"
DEFAULT_OUTPUTS_DIR = SCRIPT_DIR / "../llm_prompting/outputs"


def parse_pitfall_number(raw: str) -> int:
    """Normalizes a pitfall identifier given on the command line ('4',
    '04', 'P04', 'p4', ...) to a plain int, e.g. 4.
    """
    digits = raw.strip().upper().lstrip("P")
    return int(digits)


def batch_paths_for_pitfall(pitfall_number: int) -> tuple[Path, Path]:
    """Derives the default --requests / --results paths for a given
    pitfall number, following the project's naming convention:
        <script_dir>/../llm_prompting/batches/batch_input_<n>.jsonl
        <script_dir>/../llm_prompting/outputs/output_batch_input_<n>.jsonl
    """
    requests_path = DEFAULT_BATCHES_DIR / f"batch_input_{pitfall_number}.jsonl"
    results_path = DEFAULT_OUTPUTS_DIR / f"output_batch_input_{pitfall_number}.jsonl"
    return requests_path, results_path


# --------------------------------------------------------------------------
# Loading ontology modules and batch JSONL files
# --------------------------------------------------------------------------

def load_modules(ontology_dir: Path) -> dict[str, TurtleFile]:
    """Parses every known ontology module found in `ontology_dir`."""
    modules: dict[str, TurtleFile] = {}
    for name in MODULE_NAMES:
        path = ontology_dir / f"{name}.ttl"
        if not path.exists():
            print(f"[warn] module file not found, skipping: {path}")
            continue
        modules[name] = parse_turtle(path)
    return modules


def load_jsonl(path: Path) -> list[dict]:
    entries = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def load_jsonl_map(path: Path, key: str) -> dict[str, dict]:
    return {entry[key]: entry for entry in load_jsonl(path)}


# --------------------------------------------------------------------------
# Extracting information from a request / result pair
# --------------------------------------------------------------------------

def _get_user_text(request_entry: dict) -> str:
    """Returns the text of the last 'user' message in a request body."""
    for msg in request_entry["body"]["input"]:
        if msg.get("role") == "user":
            for c in msg.get("content", []):
                if c.get("type") == "input_text":
                    return c["text"]
    raise ValueError("no user input_text found in request body")


def extract_affected_and_prefixes(request_entry: dict) -> tuple[list[str], dict[str, str]]:
    """Pulls the 'Affected Elements' list and the @prefix declarations of
    the Turtle context snippet out of a request entry's user message.
    """
    text = _get_user_text(request_entry)

    m = AFFECTED_RE.search(text)
    if not m:
        raise ValueError("could not find 'Affected Elements' in request text")
    affected_raw = ast.literal_eval(m.group(1))

    prefixes: dict[str, str] = {}
    tm = TURTLE_BLOCK_RE.search(text)
    if tm:
        snippet = tm.group(1)
        for pm in PREFIX_RE.finditer(snippet):
            prefixes[pm.group(1)] = pm.group(2)

    return affected_raw, prefixes


def extract_response_json(result_entry: dict) -> dict:
    """Extracts and parses the structured-output JSON produced by the LLM."""
    body = result_entry["response"]["body"]
    for item in body.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    return json.loads(c["text"])
    raise ValueError("no output_text message found in response body")


def resolve_term(raw: str, prefixes: dict[str, str]) -> URIRef | None:
    """Resolves a term as written in the LLM prompt ('foaf:Document',
    ':APIDescriptionArtifact', '<http://...>') to a full URIRef, using the
    prefixes declared in that request's own Turtle context snippet.
    """
    raw = raw.strip()
    if raw.startswith("<") and raw.endswith(">"):
        return URIRef(raw[1:-1])
    if ":" not in raw:
        return None
    prefix, local = raw.split(":", 1)
    ns = prefixes.get(prefix)
    if ns is None:
        return None
    return URIRef(ns + local)


# --------------------------------------------------------------------------
# Generic Turtle-editing helpers
# --------------------------------------------------------------------------

def remove_term_everywhere(modules: dict[str, TurtleFile], term: URIRef) -> list[str]:
    """Removes every block that mentions `term` (as subject, predicate or
    object, including inside nested blank-node structures) from every
    module. Returns the names of modules that were actually changed.
    """
    changed_files = []
    for name, tf in modules.items():
        blocks_to_remove = [b for b in tf.blocks if block_contains_term(tf, b, term)]
        if blocks_to_remove:
            for b in blocks_to_remove:
                tf.blocks.remove(b)
            changed_files.append(name)
    return changed_files


def write_turtle_file(tf: TurtleFile) -> None:
    """Serializes a TurtleFile's current blocks back to disk, keeping only
    the prefixes that are still actually used.
    """
    prefixes = used_prefixes(tf.blocks, tf.prefixes)
    header = "\n".join(f"@prefix {p}: <{iri}> ." for p, iri in prefixes.items())
    content = header + "\n\n" + "\n\n".join(tf.blocks) + "\n"
    tf.path.write_text(content, encoding="utf-8")


# --------------------------------------------------------------------------
# Pitfall fixer registry
# --------------------------------------------------------------------------

class PitfallFixer:
    """Base class for a fixer handling one OOPS! pitfall id."""

    pitfall_id: str = ""

    def apply(
        self,
        modules: dict[str, TurtleFile],
        affected_terms: list[URIRef | None],
        response_json: dict,
        context_prefixes: dict[str, str],
    ) -> list[str]:
        """Applies the fix in-place on `modules`. Returns the list of module
        names that were actually changed.
        """
        raise NotImplementedError


FIXERS: dict[str, PitfallFixer] = {}


def register_fixer(pitfall_id: str):
    def decorator(cls):
        FIXERS[pitfall_id] = cls()
        return cls
    return decorator


@register_fixer("P04")
class P04UnconnectedElementFixer(PitfallFixer):
    """P04 -- Creating unconnected ontology elements.

    The LLM was asked to decide, for one isolated term at a time, whether
    it is a genuine problem (the term should be removed entirely) or a
    false positive (nothing to do). We simply follow that verdict.
    """

    pitfall_id = "P04"

    def apply(self, modules, affected_terms, response_json, context_prefixes):
        if not response_json.get("remove", False):
            return []

        changed_files: list[str] = []
        for term in affected_terms:
            if term is None:
                print("  ! could not resolve one of the affected elements, skipping it")
                continue
            changed_files.extend(remove_term_everywhere(modules, term))
        return sorted(set(changed_files))


@register_fixer("P08")
class P08MissingAnnotationsFixer(PitfallFixer):
    """P08 -- Missing annotations.

    The LLM generates whichever of rdfs:label / rdfs:comment was missing
    for the affected term (empty string means it was already present and
    nothing needs to be generated for it). Each non-empty value is added
    as a new triple to the *first* block, in the detected module, where
    the affected element is that block's own (top-level) subject.
    """

    pitfall_id = "P08"

    def apply(self, modules, affected_terms, response_json, context_prefixes):
        label = response_json.get("label", "") or ""
        comment = response_json.get("comment", "") or ""
        if not label and not comment:
            return []

        changed_files: list[str] = []
        for term in affected_terms:
            if term is None:
                print("  ! could not resolve one of the affected elements, skipping it")
                continue

            found = find_first_subject_block(modules, term)
            if found is None:
                print(f"  ! {term} is not the subject of any block in the known modules, skipping it")
                continue

            module_name, block = found
            tf = modules[module_name]
            new_block = add_annotation_triples(block, label, comment)
            if new_block == block:
                continue

            idx = tf.blocks.index(block)
            tf.blocks[idx] = new_block
            ensure_prefix(tf, "rdfs", "http://www.w3.org/2000/01/rdf-schema#")
            changed_files.append(module_name)

        return sorted(set(changed_files))


@register_fixer("P13")
class P13InverseRelationshipFixer(PitfallFixer):
    """P13 -- Inverse relationships not explicitly declared.

    If the LLM identified an existing property that serves as the
    (informal) inverse of the affected property, an owl:inverseOf triple
    naming it is appended to the affected property's own block. OWL's
    semantics make owl:inverseOf symmetric under entailment, so declaring
    it on this one side is sufficient -- matching how the LLM was only
    ever asked to find one direction.

    The inverse CURIE the LLM returns is phrased using the fully named
    prefix from its prompt context (e.g. 'tbox:resolveBefore'), but if the
    inverse property lives in the very module we're editing, that module
    likely refers to its own terms via the empty/default prefix instead
    (same situation as in the P07 fixer). curie_for() resolves this
    correctly by reading the target module's *actual* declared prefixes;
    normalize_self_prefix() is kept as an extra safety net in case that
    lookup falls back to the raw, fully-prefixed string.
    """

    pitfall_id = "P13"

    def apply(self, modules, affected_terms, response_json, context_prefixes):
        if not response_json.get("exists", False):
            return []

        inverse_raw = response_json.get("inverse", "")
        if not inverse_raw:
            return []

        changed_files: list[str] = []
        for term in affected_terms:
            if term is None:
                print("  ! could not resolve one of the affected elements, skipping it")
                continue

            found = find_first_subject_block(modules, term)
            if found is None:
                print(f"  ! {term} is not the subject of any block in the known modules, skipping it")
                continue

            module_name, block = found
            tf = modules[module_name]

            inverse_term = resolve_term(inverse_raw, context_prefixes)
            if inverse_term is not None:
                inverse_curie = curie_for(inverse_term, tf.prefixes) or inverse_raw
            else:
                inverse_curie = inverse_raw
            inverse_curie = normalize_self_prefix(inverse_curie, module_name)

            new_block = add_object_triple(block, "owl:inverseOf", inverse_curie)
            if new_block == block:
                continue

            idx = tf.blocks.index(block)
            tf.blocks[idx] = new_block
            tf.block_subject[new_block] = tf.block_subject.pop(block)
            tf.block_triples[new_block] = tf.block_triples.pop(block, [])
            ensure_prefix(tf, "owl", "http://www.w3.org/2002/07/owl#")
            changed_files.append(module_name)

        return sorted(set(changed_files))


@register_fixer("P34")
class P34UntypedClassFixer(PitfallFixer):
    """P34 -- Untyped class.

    If the LLM confirms the affected term is genuinely used as a class
    without ever being declared as one, an '<term> a owl:Class' triple is
    added:
    - If the term is already the subject of some block (it's used
      elsewhere in the ontology, just never explicitly typed), the
      declaration is inserted as the very first predicate-object pair of
      that existing block.
    - Otherwise, a brand-new standalone block containing just the
      declaration is appended to whichever module the term's own CURIE
      prefix/namespace belongs to. If that module can't be determined
      (e.g. the term is an external/imported vocabulary term), the fix is
      skipped with a warning, since there's nowhere sensible to put it.
    """

    pitfall_id = "P34"

    def apply(self, modules, affected_terms, response_json, context_prefixes):
        if not response_json.get("missing", False):
            return []

        changed_files: list[str] = []
        for term in affected_terms:
            if term is None:
                print("  ! could not resolve one of the affected elements, skipping it")
                continue

            found = find_first_subject_block(modules, term)
            if found is not None:
                module_name, block = found
                tf = modules[module_name]
                new_block = prepend_class_declaration(block)
                idx = tf.blocks.index(block)
                tf.blocks[idx] = new_block
                tf.block_subject[new_block] = tf.block_subject.pop(block)
                tf.block_triples[new_block] = tf.block_triples.pop(block, [])
                ensure_prefix(tf, "owl", "http://www.w3.org/2002/07/owl#")
                changed_files.append(module_name)
                continue

            # No existing block for this term anywhere -- create one.
            module_name = _guess_module_for_term(term, modules)
            if module_name is None:
                print(f"  ! {term} doesn't belong to any known module (likely an "
                      f"external/imported term), skipping its class declaration")
                continue

            tf = modules[module_name]
            curie = curie_for(term, tf.prefixes)
            if curie is None:
                print(f"  ! could not render a CURIE for {term} in module "
                      f"{module_name!r}, skipping it")
                continue
            curie = normalize_self_prefix(curie, module_name)

            new_block = f"{curie} a owl:Class ."
            tf.blocks.append(new_block)
            register_new_block(tf, new_block, term)
            ensure_prefix(tf, "owl", "http://www.w3.org/2002/07/owl#")
            changed_files.append(module_name)

        return sorted(set(changed_files))


def find_first_subject_block(modules: dict[str, TurtleFile], term: URIRef) -> tuple[str, str] | None:
    """Returns (module_name, block_text) for the first block -- in module
    iteration order, then file order within that module -- whose own
    top-level subject is `term`. Returns None if no such block exists in
    any loaded module (e.g. `term` is an external vocabulary term that is
    only ever used, never defined, locally).
    """
    for name, tf in modules.items():
        for block in tf.blocks:
            if tf.block_subject.get(block) == term:
                return name, block
    return None


def ensure_prefix(tf: TurtleFile, prefix: str, iri: str) -> None:
    """Makes sure `tf` declares `prefix` (needed if we introduce a new
    predicate like rdfs:label into a module that happens not to use rdfs
    yet -- rare in this ontology, but cheap to guard against).
    """
    tf.prefixes.setdefault(prefix, iri)


def _escape_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _detect_indent(block_text: str) -> str:
    """Guesses the indentation used for continuation lines in a block, so
    that newly added triples match the surrounding style. Falls back to
    four spaces if no indented continuation line is found.
    """
    for line in block_text.split("\n")[1:]:
        stripped = line.lstrip(" ")
        if stripped and stripped != line:
            return line[: len(line) - len(stripped)]
    return "    "


def add_annotation_triples(block_text: str, label: str, comment: str) -> str:
    """Appends rdfs:label and/or rdfs:comment triples (whichever of
    `label` / `comment` is non-empty) to a Turtle block, right before its
    closing '.'. The block's existing predicate-object pairs are left
    untouched; a trailing ';' is added to the previous last pair and the
    new triple(s) take over the closing '.'.
    """
    additions = []
    if label:
        additions.append(f'rdfs:label "{_escape_literal(label)}"@en')
    if comment:
        additions.append(f'rdfs:comment "{_escape_literal(comment)}"@en')
    if not additions:
        return block_text

    text = block_text.rstrip()
    if not text.endswith("."):
        raise ValueError("block does not end with '.', refusing to edit it")
    body = text[:-1].rstrip()

    indent = _detect_indent(text)
    new_lines = [body + " ;"]
    for i, addition in enumerate(additions):
        terminator = " ." if i == len(additions) - 1 else " ;"
        new_lines.append(f"{indent}{addition}{terminator}")

    return "\n".join(new_lines)


def add_object_triple(block_text: str, predicate: str, obj: str) -> str:
    """Appends a single predicate-object triple to a block, right before
    its closing '.' -- like add_annotation_triples, but for an arbitrary
    predicate/object pair (given exactly as they should appear in the
    Turtle text, e.g. predicate='owl:inverseOf', obj=':resolveBefore')
    rather than a label/comment literal.
    """
    text = block_text.rstrip()
    if not text.endswith("."):
        raise ValueError("block does not end with '.', refusing to edit it")
    body = text[:-1].rstrip()
    indent = _detect_indent(text)
    return f"{body} ;\n{indent}{predicate} {obj} ."


def prepend_class_declaration(block_text: str) -> str:
    """Inserts '<subject> a owl:Class' as the very first predicate-object
    pair of a block's Turtle statement, pushing whatever pair was there
    before it down to second place (rather than merging into an existing
    'a ...' type list, per the P34 fix spec: it's added as its own,
    separate first triple).
    """
    match = SUBJECT_TOKEN_RE.match(block_text)
    if not match:
        raise ValueError("could not locate the subject token at the start of this block")
    subject_text = block_text[match.start():match.end()]
    rest = block_text[match.end():].lstrip()
    indent = _detect_indent(block_text)
    return f"{subject_text} a owl:Class ;\n{indent}{rest}"


@register_fixer("P07")
class P07MergedConceptsFixer(PitfallFixer):
    """P07 -- Merging different concepts in the same class.

    For each affected class, the LLM either says split=false (false
    positive -- nothing to do) or split=true and supplies full Turtle
    blocks for the concepts the class should be split into. The old
    concept's block is replaced in-place by those new blocks.

    Every entry in 'assignments' then rewires one relationship of a
    consuming class away from the old (removed) concept towards the new
    one(s). Several assignments can share the same (sourceConcept,
    property, targetOldConceptId) -- meaning that single original
    relationship should fan out to more than one of the split concepts
    (per the pitfall instructions: "relationships do not have to be
    partitioned disjunctly") -- in which case all of them are kept as
    comma-separated objects of that property.
    """

    pitfall_id = "P07"

    def apply(self, modules, affected_terms, response_json, context_prefixes):
        changed_files: set[str] = set()

        # oldConceptId (as written by the LLM) -> whether it was actually split.
        split_info: dict[str, bool] = {}

        for concept in response_json.get("concepts", []):
            old_id = concept["oldConceptId"]
            if not concept.get("split", False):
                split_info[old_id] = False
                continue

            old_term = resolve_term(old_id, context_prefixes)
            if old_term is None:
                print(f"  ! could not resolve oldConceptId {old_id!r}, skipping its split")
                split_info[old_id] = False
                continue

            new_terms: list[URIRef] = []
            new_blocks: list[str] = []
            for nc in concept.get("newConcepts", []):
                new_term = resolve_term(nc["conceptId"], context_prefixes)
                if new_term is None:
                    print(f"  ! could not resolve new conceptId {nc['conceptId']!r}, skipping it")
                    continue
                new_terms.append(new_term)
                new_blocks.append(nc["block"].strip())

            found = find_first_subject_block(modules, old_term)
            if found is not None:
                module_name, old_block = found
            else:
                module_name, old_block = _guess_module_for_curie(modules, old_id), None

            if module_name is None:
                print(f"  ! {old_term} is not the subject of any block and its module "
                      f"could not be guessed, skipping its split")
                split_info[old_id] = False
                continue

            tf = modules[module_name]
            # The LLM was shown the target module's own terms fully prefixed
            # (e.g. 'ce:GovernanceActivity'), but the module files themselves
            # reference their own terms via the empty/default prefix (':').
            # Rewrite the generated blocks to match that local convention.
            new_blocks = [normalize_self_prefix(b, module_name) for b in new_blocks]

            if old_block is not None:
                idx = tf.blocks.index(old_block)
                tf.blocks[idx:idx + 1] = new_blocks
                del tf.block_subject[old_block]
                tf.block_triples.pop(old_block, None)
            else:
                tf.blocks.extend(new_blocks)

            for term, block in zip(new_terms, new_blocks):
                register_new_block(tf, block, term)
            ensure_prefixes_used_in_blocks(tf, new_blocks)

            changed_files.add(module_name)
            split_info[old_id] = True

        # Group assignments so a single relationship can legitimately fan
        # out to several of the newly split concepts.
        groups: dict[tuple[str, str, str], list[str]] = {}
        for a in response_json.get("assignments", []):
            key = (a["sourceConcept"], a["property"], a["targetOldConceptId"])
            groups.setdefault(key, []).append(a["assignedConceptId"])

        for (source_id, _property_curie, old_target_id), assigned_ids in groups.items():
            if split_info.get(old_target_id) is False:
                continue  # false positive: original relationship is left untouched

            source_term = resolve_term(source_id, context_prefixes)
            old_target_term = resolve_term(old_target_id, context_prefixes)
            if source_term is None or old_target_term is None:
                print(f"  ! could not resolve {source_id!r} or {old_target_id!r}, "
                      f"skipping this reassignment")
                continue

            found = find_first_subject_block(modules, source_term)
            if found is None:
                print(f"  ! {source_term} is not the subject of any block in the known "
                      f"modules, skipping its reassignment")
                continue
            module_name, block = found
            tf = modules[module_name]

            old_target_curie = curie_for(old_target_term, tf.prefixes) or old_target_id
            new_target_curies = []
            for raw in assigned_ids:
                term = resolve_term(raw, context_prefixes)
                new_target_curies.append(curie_for(term, tf.prefixes) if term is not None else raw)

            new_block = replace_object_in_block(block, old_target_curie, new_target_curies)
            if new_block == block:
                continue

            idx = tf.blocks.index(block)
            tf.blocks[idx] = new_block
            tf.block_subject[new_block] = tf.block_subject.pop(block)
            tf.block_triples[new_block] = tf.block_triples.pop(block, [])
            changed_files.add(module_name)

        return sorted(changed_files)


def normalize_self_prefix(text: str, module_name: str) -> str:
    """Rewrites CURIEs using a module's own named prefix (e.g. 'ce:Foo'
    appearing inside ce.ttl) to that module's default/empty-prefix form
    (':Foo'). Ontology modules declare '@prefix : <their-own-namespace> .'
    and consistently reference their own terms that way -- only
    cross-module references use the full named prefix -- but LLM-generated
    blocks are produced from a context snippet that fully prefixes
    everything, so this normalization step is needed before insertion.
    """
    pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(module_name) + r":(?=[A-Za-z_])")
    return pattern.sub(":", text)


def register_new_block(tf: TurtleFile, block_text: str, subject: URIRef) -> None:
    """Registers a freshly inserted block's subject and triples in a
    TurtleFile's bookkeeping dicts, so that later helper calls (e.g.
    find_first_subject_block for a P07 assignment that targets a concept
    created earlier in the same fix) see it exactly like a block that was
    already there when the file was parsed.
    """
    tf.block_subject[block_text] = subject
    header = "\n".join(f"@prefix {p}: <{iri}> ." for p, iri in tf.prefixes.items())
    g_block = Graph()
    try:
        g_block.parse(data=header + "\n\n" + block_text, format="turtle")
        tf.block_triples[block_text] = list(g_block)
    except Exception as exc:
        print(f"  ! could not parse newly inserted block for {subject} ({exc}); "
              f"it was still written to the file, but later lookups may miss it")
        tf.block_triples[block_text] = []


def ensure_prefixes_used_in_blocks(tf: TurtleFile, blocks: list[str]) -> None:
    """Makes sure every prefix referenced in `blocks` is declared in `tf`,
    using KNOWN_PREFIXES as the source of truth for their namespaces.
    """
    text = "\n".join(blocks)
    for m in USED_PREFIX_RE.finditer(text):
        prefix = m.group(1) or ""
        if prefix and prefix not in tf.prefixes and prefix in KNOWN_PREFIXES:
            tf.prefixes[prefix] = KNOWN_PREFIXES[prefix]


def curie_for(term: URIRef, prefixes: dict[str, str]) -> str | None:
    """Inverse of resolve_term: renders a URIRef back as 'prefix:local'
    using a given prefix table, or None if no declared prefix matches.
    """
    iri = str(term)
    for prefix, ns in prefixes.items():
        if iri.startswith(ns):
            return f"{prefix}:{iri[len(ns):]}"
    return None


def _guess_module_for_term(term: URIRef, modules: dict[str, TurtleFile]) -> str | None:
    """Like _guess_module_for_curie, but for a term we only have as a
    resolved URIRef (no CURIE string on hand): matches its IRI against
    KNOWN_PREFIXES' namespaces to find which module it belongs to.
    """
    iri = str(term)
    for prefix, ns in KNOWN_PREFIXES.items():
        if iri.startswith(ns) and prefix in modules:
            return prefix
    return None


def _guess_module_for_curie(modules: dict[str, TurtleFile], curie: str) -> str | None:
    """Fallback for when a term isn't the subject of any known block: guesses
    its home module from its CURIE prefix (module names match the ABox
    prefixes: ae, ce, fe, ie, le, pe, te).
    """
    if ":" not in curie:
        return None
    prefix = curie.split(":", 1)[0]
    return prefix if prefix in modules else None


def replace_object_in_block(block_text: str, old_curie: str, new_curies: list[str]) -> str:
    """Replaces the first standalone occurrence of `old_curie` in a block's
    text with a comma-separated list of `new_curies` (Turtle's syntax for
    multiple objects of the same predicate). If `old_curie` isn't found,
    the block is returned unchanged and a warning is printed.
    """
    if not new_curies:
        return block_text
    pattern = re.compile(r"(?<![A-Za-z0-9_.])" + re.escape(old_curie) + r"(?![A-Za-z0-9_.-])")
    replacement = ", ".join(dict.fromkeys(new_curies))  # de-duplicate, keep order
    new_text, count = pattern.subn(replacement, block_text, count=1)
    if count == 0:
        print(f"  ! could not find {old_curie!r} in the target block, skipping this reassignment")
        return block_text
    return new_text


# --------------------------------------------------------------------------
# Main driver
# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply LLM-suggested OOPS! pitfall fixes to the FSL ontology modules."
    )
    parser.add_argument("--ontology-dir", default=DEFAULT_ONTOLOGY_DIR, type=Path)
    parser.add_argument("--pitfall", type=str, default=None,
                         help="Pitfall number/id (e.g. '4' or 'P04'). Used to derive default "
                              "--requests/--results paths following the batch_input_<n>.jsonl / "
                              "output_batch_input_<n>.jsonl naming convention. Not needed if "
                              "both --requests and --results are given explicitly.")
    parser.add_argument("--requests", default=None, type=Path,
                         help="JSONL file with the batch request bodies (one per line). "
                              "Defaults to batches/batch_input_<pitfall>.jsonl.")
    parser.add_argument("--results", default=None, type=Path,
                         help="JSONL file with the batch result bodies (one per line). "
                              "Defaults to outputs/output_batch_input_<pitfall>.jsonl. "
                              "Point this at a trimmed file (e.g. via 'head -n 1') to test "
                              "on just the first line.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Report planned changes without writing any files.")
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N lines of --results (for quick testing).")
    args = parser.parse_args()

    if args.requests is None or args.results is None:
        if args.pitfall is None:
            parser.error("either --pitfall or both --requests and --results must be given")
        default_requests, default_results = batch_paths_for_pitfall(parse_pitfall_number(args.pitfall))
        if args.requests is None:
            args.requests = default_requests
        if args.results is None:
            args.results = default_results

    modules = load_modules(args.ontology_dir)
    requests_by_id = load_jsonl_map(args.requests, key="custom_id")
    results = load_jsonl(args.results)
    if args.limit is not None:
        results = results[: args.limit]

    dirty_modules: set[str] = set()

    for result_entry in results:
        custom_id = result_entry["custom_id"]
        pitfall_id = custom_id.split("_", 1)[0]

        fixer = FIXERS.get(pitfall_id)
        if fixer is None:
            print(f"[skip]  {custom_id}: no fixer registered for pitfall {pitfall_id}")
            continue

        request_entry = requests_by_id.get(custom_id)
        if request_entry is None:
            print(f"[skip]  {custom_id}: no matching request entry found")
            continue

        try:
            affected_raw, context_prefixes = extract_affected_and_prefixes(request_entry)
            response_json = extract_response_json(result_entry)
        except Exception as exc:
            print(f"[error] {custom_id}: failed to parse request/response ({exc})")
            continue

        affected_terms = [resolve_term(t, context_prefixes) for t in affected_raw]

        try:
            changed = fixer.apply(modules, affected_terms, response_json, context_prefixes)
        except Exception as exc:
            print(f"[error] {custom_id}: fixer raised an exception ({exc})")
            continue

        if changed:
            print(f"[fixed] {custom_id} ({pitfall_id}): applied fix for {affected_raw} in {changed}")
            dirty_modules.update(changed)
        else:
            print(f"[noop]  {custom_id} ({pitfall_id}): no change (false positive or nothing to do)")

    if args.dry_run:
        print(f"\nDry run: would write {len(dirty_modules)} module file(s): {sorted(dirty_modules)}")
        return

    for name in dirty_modules:
        write_turtle_file(modules[name])
    print(f"\nWrote {len(dirty_modules)} module file(s): {sorted(dirty_modules)}")


if __name__ == "__main__":
    main()