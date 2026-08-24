import subprocess
import sys
from pathlib import Path

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