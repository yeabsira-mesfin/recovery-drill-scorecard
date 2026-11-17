"""Local inventory for Recovery Drill Scorecard."""
import argparse
import json
from pathlib import Path
import sys


def inventory(document):
    if not isinstance(document, dict):
        raise ValueError("expected a JSON object")
    payload = document.get("input", document)
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError("records must be an array")
    if any(not isinstance(row, dict) for row in payload["records"]):
        raise ValueError("each record must be an object")
    return {"records": len(payload["records"])}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "catalog":
        from domain import PROJECT, example_payload, validate_payload
        from evidence_catalog.cli import main as catalog_main
        return catalog_main(validate_payload, PROJECT, sys.argv[2:], sample=example_payload)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inventory").add_argument("file", type=Path)
    args = parser.parse_args()
    try:
        result = inventory(json.loads(args.file.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
