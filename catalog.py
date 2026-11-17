"""Manage this project's local evidence catalog."""

from domain import PROJECT, example_payload, validate_payload
from evidence_catalog.cli import main


if __name__ == "__main__":
    raise SystemExit(main(validate_payload, PROJECT, sample=example_payload))
