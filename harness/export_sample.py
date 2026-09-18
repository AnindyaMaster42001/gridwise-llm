"""Export a complete public request and organizer reference without shell quoting."""
import argparse
import json
from pathlib import Path

from harness.judge import ROOT, load_cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", default="SAMPLE-01")
    parser.add_argument("--directory", type=Path, default=ROOT / "harness/reports")
    args = parser.parse_args()
    cases = load_cases(ROOT / "tests/data/public_samples.json")
    case = next((c for c in cases if c["id"] == args.id), None)
    if case is None: parser.error("unknown sample id")
    args.directory.mkdir(parents=True, exist_ok=True)
    for name, content in (("request.json",case["input"]),("reference.json",case["expected_output"])):
        path = args.directory / name
        path.write_text(json.dumps(content, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
