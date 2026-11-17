"""Evaluate decision examples in isolated temporary files."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", action="store_true")
    args = parser.parse_args()
    paths = sorted((ROOT / "examples").glob("*/*.json"))
    if len(paths) != 72:
        parser.error(f"expected 72 examples, found {len(paths)}")
    failed = 0
    with tempfile.TemporaryDirectory() as temp:
        sample = Path(temp) / "input.json"
        for path in paths:
            case = json.loads(path.read_text(encoding="utf-8"))
            if args.inventory:
                continue
            sample.write_text(json.dumps(case["input"]), encoding="utf-8")
            try:
                result = subprocess.run([sys.executable, str(ROOT / "drill.py"), "score", str(sample)],
                                        capture_output=True, text=True, timeout=10, cwd=temp)
                if result.returncode:
                    raise ValueError(result.stderr.strip())
                observed = json.dumps(json.loads(result.stdout), sort_keys=True)
                if observed != json.dumps(case["expected"], sort_keys=True):
                    raise ValueError("unexpected decision: " + observed)
            except (ValueError, subprocess.TimeoutExpired) as error:
                print(f"{path.relative_to(ROOT)}: {error}", file=sys.stderr)
                failed += 1
    print(f"{len(paths)} examples loaded" if args.inventory else f"{len(paths)-failed}/{len(paths)} passed")
    return int(bool(failed))


if __name__ == "__main__":
    sys.exit(main())
