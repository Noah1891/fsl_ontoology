#!/usr/bin/env python3
"""Print a clear, human-readable summary of what a versioning run will propose.

Exists to make the gap between detect_new_releases.py's findings (everything
currently missing) and what a given run actually acts on (one evidence file)
visible in CI logs, instead of leaving it implicit.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from common.jsonio import read_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()

    evidence = read_json(args.evidence)

    print("=" * 70)
    print("This run will propose ONE version addition:")
    print(f"  Entity:      {evidence['parentEntity']} ({evidence['entityKind']})")
    print(f"  Version:     {evidence['version']}")
    print(f"  Released:    {evidence['releaseDate']}")
    print(f"  Predecessor: {evidence['predecessor']}")
    print(f"  Source:      {evidence['officialSource']}")
    print(f"  Target file: {evidence['targetModule']}")
    print()
    print("Note: 'detect' above may have found other missing versions too --")
    print("only this evidence file is processed by this run. Other candidates")
    print("need their own evidence file; they are not yet auto-looped into the")
    print("pipeline (see versioning/README.md).")
    print("=" * 70)


if __name__ == "__main__":
    main()
