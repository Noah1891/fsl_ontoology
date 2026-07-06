#!/usr/bin/env python3
"""
Extract the prompt (instructions + developer/user messages) for a given
custom_id from a JSONL batch file (one JSON object per line) into a
plain .txt file.

Usage:
    python extract_prompt.py <jsonl_file> <custom_id> [output_dir]

Example:
    python extract_prompt.py batch.jsonl P07__ce_PlanningAndManagementActivity ./out
"""

import json
import sys
import os


def extract_prompt(jsonl_path, custom_id, output_dir="."):
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("custom_id") != custom_id:
                continue

            body = obj.get("body", {})
            instructions = body.get("instructions", "")
            input_list = body.get("input", [])

            parts = []
            if instructions:
                parts.append("=== INSTRUCTIONS ===\n" + instructions.strip())

            for msg in input_list:
                role = msg.get("role", "unknown")
                contents = msg.get("content", [])
                text_chunks = [
                    c.get("text", "")
                    for c in contents
                    if c.get("type") == "input_text"
                ]
                joined = "\n".join(text_chunks).strip()
                parts.append(f"=== {role.upper()} ===\n{joined}")

            # Extract the JSON schema, if present (body.text.format.schema)
            schema = (
                body.get("text", {})
                .get("format", {})
                .get("schema")
            )
            if schema:
                schema_str = json.dumps(schema, indent=2, ensure_ascii=False)
                parts.append("=== JSON SCHEMA ===\n" + schema_str)

            os.makedirs(output_dir, exist_ok=True)
            out_path = os.path.join(output_dir, f"{custom_id}.txt")
            with open(out_path, "w", encoding="utf-8") as out:
                out.write("\n\n".join(parts) + "\n")

            print(f"Written: {out_path}")
            return True

    print(f"custom_id '{custom_id}' not found in {jsonl_path}")
    return False


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python extract_prompt.py <jsonl_file> <custom_id> [output_dir]")
        sys.exit(1)

    jsonl_file = sys.argv[1]
    cid = sys.argv[2]
    out_dir = sys.argv[3] if len(sys.argv) > 3 else "."

    ok = extract_prompt(jsonl_file, cid, out_dir)
    sys.exit(0 if ok else 1)