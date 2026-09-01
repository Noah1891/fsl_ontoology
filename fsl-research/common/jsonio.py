"""Shared JSON file I/O used across experiment pipelines."""

import json
from pathlib import Path


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=indent) + "\n", encoding="utf-8")
