# LLM-assisted versioning experiment

This experiment extends the existing SAREF-inspired Python/CPython versioning proof of concept, and generalizes to any tracked language/tool pair -- `state/tracked-entities.json` also tracks Java (`pe:Java`) and OpenJDK (`te:OpenJDK`) as a second, independent example. Neither has any version history in FSL yet, so `detect` will report their oldest feed release as `needs-review` (no predecessor exists to chain off of) until a human seeds one starting version for each -- see "Detecting new releases" below. It evaluates whether an LLM can propose a **single, reviewable version addition** for an entity that already exists in FSL.

The LLM is not an authority for release facts and must not edit FSL directly. Each run follows this pipeline:

```text
tracked-entities.json + release feed        <- detect_new_releases.py (no LLM)
  -> candidate release evidence
  -> structured LLM response
  -> deterministic schema and ontology validation
  -> candidate patch
  -> human review
```

## Layout

- `state/tracked-entities.json` lists the FSL entities this experiment watches and where to check for new releases. Each entry names the FSL parent entity, its `targetModule`, a release `feed` (`type` + feed-specific `product`), and an `officialSourceTemplate` (may use `{version}`, `{version_nodot}`, or `{version_safe}` -- whichever fits that entity's URL scheme). Adding a new language/tool pair is: (1) add its entities here, (2) seed at least one existing version for each in its `targetModule` (an entity with zero tracked versions has no predecessor to chain the first backfilled candidate off of -- see Java/OpenJDK, tracked but not yet seeded, in "Detecting new releases" below).
- `src/` contains version-controlled, normalised release evidence and its schema.
- `prompts/` contains the human-readable LLM instructions.
- `schemas/` contains the required structured LLM output contract.
- `scripts/` contains local tooling that detects candidates, builds requests, validates output, and prepares review artefacts.
- `../results/versioning/` contains generated, reproducible run outputs. It must never be used as source input.

## Detecting new releases (no LLM)

From the repository root, diff FSL's tracked entities against their release feeds:

```bash
make -C fsl-research/saref-experiment versioning-detect
```

This is deterministic HTTP + SPARQL only. It writes one candidate evidence file per missing version to `fsl-research/saref-experiment/results/versioning/detected/<runId>.json` -- e.g. `python-3.13.json` -- shaped like `src/release-evidence.schema.json`, but the `predecessor` link and `officialSource` URL are inferred by convention and **must be confirmed by a human before use as LLM input.**

It defaults to full backfill: every release the feed has that FSL doesn't yet track for that entity, not just the newest. Only the oldest missing version(s) can be turned into an immediately mergeable candidate -- `hasPredecessor` is a linked list, so a version whose predecessor is itself still only a candidate (not yet merged) will fail `render_and_validate.py`'s `predecessor-exists-in-module` check until that predecessor lands first. That's expected: a large gap gets closed one merge at a time, oldest first, across successive runs, rather than silently staying a permanent gap. The very oldest release in a feed (no predecessor exists anywhere) is reported as `needs-review` and is never auto-written -- seed it by hand once, the same way Python's 2.7/3.6/3.12 baseline was seeded before this pipeline existed. Java/OpenJDK are tracked but not yet seeded, so today `detect` will only ever report their oldest release as `needs-review` for them -- add one hand-picked starting version for `pe:Java` and `te:OpenJDK` in `ontologies/pe.ttl`/`te.ttl` to unblock automatic backfill for those two. Pass `EXTRA=--frontier-only` to go back to reporting only releases newer than FSL's current newest, if you'd rather not see the full gap.

## Running the full pipeline (any tracked entity)

From the repository root, pass `RUN_ID=<parentEntity-local-lowercased>-<tag>` to target any evidence file under `src/` -- required, there's no default (e.g. `RUN_ID=python-3.13`, the checked-in example, or `RUN_ID=cpython-3.7` once its evidence file exists):

```bash
make -C fsl-research/saref-experiment versioning-request RUN_ID=python-3.13           # build the LLM request (no LLM call)
make -C fsl-research/saref-experiment versioning-run RUN_ID=python-3.13 MOCK=1        # replay a fixture response, no OpenAI call
make -C fsl-research/saref-experiment versioning-validate RUN_ID=python-3.13          # parse + validate + render a candidate patch
make -C fsl-research/saref-experiment versioning-pr RUN_ID=python-3.13                # dry run: prints what a PR would look like
```

- `versioning-request` only generates `fsl-research/saref-experiment/results/versioning/batches/<RUN_ID>.jsonl`; it does not call an LLM or change an ontology module.
- `versioning-run` calls OpenAI's Batch API and needs `OPENAI_API_KEY` -- pass `MOCK=1` to instead replay `scripts/fixtures/<RUN_ID>.response.json`, so the rest of the pipeline can be exercised without spending real API calls.
- `versioning-validate` never writes to the real ontology module. It checks the response against its JSON schema and its evidence, parses `turtleBlock`, and checks module-level invariants (predecessor exists, no duplicate version tag, OWL-consistent) against a scratch copy of the target module. Only on a full pass does it write a patched module, a unified diff, and review notes to `results/versioning/candidate-patches/`; any failure writes only a validation report to `results/versioning/validation/`.
- `versioning-pr` prints the branch/commit/PR it would create. Pass `PUSH=1` to actually branch, commit, and push; `PR=1` (implies `PUSH=1`) to also open a GitHub PR via `gh`. Nothing here merges anything -- a human reviews and merges the opened PR.

## What the CI does automatically

`.github/workflows/experiment-saref.yml` runs `detect` for real (full backfill, every entity in `tracked-entities.json`) on every push, writing candidate evidence only into that run's own ephemeral `results/versioning/detected/` -- nothing here is ever committed to `main`. Before building a request, it skips any candidate that already has a batch record in the `pipeline-state` branch (pending, completed, or dispatched -- status doesn't matter), so an unmerged or still-in-flight candidate is never silently rebuilt and resubmitted on a later push; only genuinely new candidates get an LLM request built. `build_batch_request.py` embeds the full evidence JSON verbatim into that request's body, and Submit persists the request body to `pipeline-state` regardless (see `common/pipeline_state.py`) -- so the later, cron-triggered Dispatch run recovers each candidate's evidence back out of its own persisted request instead of needing anything committed to `main`. A push therefore never lands anything outside the usual candidate-patch -> combined PR -> human-review path; only that final ontology patch ever reaches `main`, via a human merging the PR.

## Safety boundary

Only evidence for an existing FSL entity may be processed. A response is a candidate until a human reviews it and the deterministic parsing, module-invariant, and OWL-consistency validation stages pass. Automation stops at an opened pull request; nothing in this pipeline merges an ontology change to `main` without human review.
