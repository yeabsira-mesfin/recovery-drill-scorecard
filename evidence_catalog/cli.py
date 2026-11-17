"""Public command routing for the offline evidence catalog."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .bundles import import_bundle, inspect_bundle, write_bundle
from .codec import atomic_write, load_records, render_records
from .quality import inspect_quality
from .query import filter_records, group_counts, histogram, page, project, summary
from .reports import csv_table, render_report
from .snapshots import compare_files, restore_snapshot, save_snapshot, verify_catalog
from .storage import Catalog
from .validation import ValidationError, batch, digest, integer
from .web import serve


def add_filters(parser):
    parser.add_argument("--tag")
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--search")


def add_database(parser, tenant=True):
    parser.add_argument("--db", required=True, type=Path)
    if tenant:
        parser.add_argument("--tenant", required=True)


def add_output(parser, formats):
    parser.add_argument("--format", choices=formats, default=formats[0])
    parser.add_argument("--output", type=Path)


def parser_for(project_name):
    parser = argparse.ArgumentParser(description=project_name + " evidence catalog")
    commands = parser.add_subparsers(dest="command", required=True)
    sample = commands.add_parser("sample", help="print a synthetic import envelope")
    sample.add_argument("--tenant", default="demo")
    init = commands.add_parser("init", help="create or validate a project database")
    add_database(init, tenant=False)
    ingest = commands.add_parser("import", help="atomically import evidence records")
    add_database(ingest, tenant=False)
    ingest.add_argument("file", type=Path)
    ingest.add_argument("--format", choices=("json", "jsonl", "csv"), default="json")
    ingest.add_argument("--dry-run", action="store_true")
    validate = commands.add_parser("validate", help="validate an import without storage")
    validate.add_argument("file", type=Path)
    validate.add_argument("--format", choices=("json", "jsonl", "csv"), default="json")
    records = commands.add_parser("list", help="filter and paginate tenant records")
    add_database(records)
    add_filters(records)
    records.add_argument("--sort", choices=("id", "observed_at"), default="id")
    records.add_argument("--descending", action="store_true")
    records.add_argument("--offset", type=int, default=0)
    records.add_argument("--limit", type=int, default=100)
    get = commands.add_parser("get", help="read one record and its current checksum")
    add_database(get)
    get.add_argument("id")
    retag = commands.add_parser("retag", help="update tags with an optimistic checksum guard")
    add_database(retag)
    retag.add_argument("id")
    retag.add_argument("--tag", action="append", default=[])
    retag.add_argument("--expected-checksum", required=True)
    export = commands.add_parser("export", help="export all matching records")
    add_database(export)
    add_filters(export)
    add_output(export, ("json", "jsonl", "csv"))
    report = commands.add_parser("report", help="render a summary with evidence")
    add_database(report)
    add_filters(report)
    add_output(report, ("json", "csv", "html"))
    stats = commands.add_parser("stats", help="summarize dates, fields and tags")
    add_database(stats)
    add_filters(stats)
    quality = commands.add_parser("quality", help="inspect evidence completeness and freshness")
    add_database(quality)
    quality.add_argument("--as-of", required=True)
    quality.add_argument("--max-age-days", type=int, default=30)
    quality.add_argument("--required-tag", action="append", default=[])
    quality.add_argument("--required-field", action="append", default=[])
    select = commands.add_parser("select", help="project fields as JSON or spreadsheet-safe CSV")
    add_database(select)
    add_filters(select)
    select.add_argument("--field", action="append", required=True)
    add_output(select, ("json", "csv"))
    group = commands.add_parser("group", help="count values of a nested field")
    add_database(group)
    group.add_argument("--field", required=True)
    bins = commands.add_parser("histogram", help="bucket integer evidence measurements")
    add_database(bins)
    bins.add_argument("--field", required=True)
    bins.add_argument("--width", required=True, type=int)
    snapshot = commands.add_parser("snapshot", help="write a deterministic checksummed snapshot")
    add_database(snapshot)
    snapshot.add_argument("output", type=Path)
    compare = commands.add_parser("diff", help="compare snapshots of the same tenant")
    compare.add_argument("before", type=Path)
    compare.add_argument("after", type=Path)
    restore = commands.add_parser("restore-snapshot", help="import a verified snapshot")
    add_database(restore, tenant=False)
    restore.add_argument("file", type=Path)
    audit = commands.add_parser("audit", help="read the tenant event history")
    add_database(audit)
    audit.add_argument("--verify", action="store_true")
    health = commands.add_parser("health", help="check SQLite integrity and schema")
    add_database(health, tenant=False)
    backup = commands.add_parser("backup", help="create a consistent database backup")
    add_database(backup, tenant=False)
    backup.add_argument("output", type=Path)
    viewer = commands.add_parser("serve", help="view one tenant over loopback HTTP")
    add_database(viewer)
    viewer.add_argument("--port", type=int, default=8765)
    bundle = commands.add_parser("bundle", help="package checksummed evidence and reports")
    add_database(bundle)
    bundle.add_argument("output", type=Path)
    inspect = commands.add_parser("inspect-bundle", help="validate all bundle representations")
    inspect.add_argument("file", type=Path)
    load_bundle = commands.add_parser("import-bundle", help="restore a validated evidence package")
    add_database(load_bundle, tenant=False)
    load_bundle.add_argument("file", type=Path)
    return parser


def filtered(catalog, args):
    return filter_records(catalog.records(args.tenant), tag=args.tag,
                          since=args.since, until=args.until, search=args.search)


def output(content, destination):
    if destination is None:
        sys.stdout.write(content)
    else:
        atomic_write(destination, content)


def execute(args, validator, project_name):
    command = args.command
    if command == "validate":
        rows = batch(load_records(args.file, args.format), validator)
        return {"valid": True, "records": len(rows)}
    if command == "diff":
        return compare_files(args.before, args.after, project_name, validator)
    if command == "inspect-bundle":
        snapshot = inspect_bundle(args.file, project_name, validator)
        return {"valid": True, "tenant": snapshot["tenant"], "records": len(snapshot["records"])}
    catalog = Catalog(args.db, validator, project_name)
    destination = getattr(args, "output", None)
    if destination is not None and destination.resolve() == args.db.resolve():
        raise ValidationError("output must not overwrite the database")
    if command == "init":
        catalog.initialize()
        return catalog.health()
    if command == "import":
        rows = load_records(args.file, args.format)
        if args.dry_run:
            checked = batch(rows, validator)
            return {"valid": True, "records": len(checked), "stored": False}
        return catalog.ingest(rows)
    if command == "list":
        return page(filtered(catalog, args), args.sort, args.descending, args.offset, args.limit)
    if command == "get":
        row = catalog.get(args.tenant, args.id)
        return {"record": row, "checksum": digest(row)}
    if command == "retag":
        return catalog.retag(args.tenant, args.id, args.tag, args.expected_checksum)
    if command == "export":
        content = render_records(filtered(catalog, args), args.format)
        output(content, args.output)
        return None
    if command == "report":
        content = render_report(project_name, args.tenant, filtered(catalog, args), args.format)
        output(content, args.output)
        return None
    if command == "stats":
        return summary(filtered(catalog, args))
    if command == "quality":
        return inspect_quality(catalog.records(args.tenant), args.as_of, args.max_age_days,
                               args.required_tag, args.required_field)
    if command == "select":
        rows = project(filtered(catalog, args), args.field)
        content = csv_table(rows, args.field) if args.format == "csv" else json.dumps(rows, indent=2) + "\n"
        output(content, args.output)
        return None
    if command == "group":
        return group_counts(catalog.records(args.tenant), args.field)
    if command == "histogram":
        return histogram(catalog.records(args.tenant), args.field, args.width)
    if command == "snapshot":
        return save_snapshot(args.output, project_name, args.tenant,
                             catalog.records(args.tenant), validator)
    if command == "restore-snapshot":
        return restore_snapshot(catalog, args.file)
    if command == "audit":
        return verify_catalog(catalog, args.tenant) if args.verify else catalog.audit(args.tenant)
    if command == "health":
        return catalog.health()
    if command == "backup":
        return catalog.backup(args.output)
    if command == "serve":
        integer(args.port, "port", 0, 65535)
        serve(catalog, args.tenant, args.port)
        return None
    if command == "bundle":
        return write_bundle(catalog, args.tenant, args.output)
    if command == "import-bundle":
        return import_bundle(catalog, args.file)
    raise ValidationError("unknown command")


def main(validator, project_name, argv=None, sample=None):
    parser = parser_for(project_name)
    args = parser.parse_args(argv)
    try:
        if args.command == "sample":
            rows = [{"id": "sample-01", "tenant": args.tenant,
                     "observed_at": "2026-01-01T00:00:00Z", "tags": ["synthetic"],
                     "payload": sample()}]
            result = {"records": batch(rows, validator)}
        else:
            result = execute(args, validator, project_name)
        if result is not None:
            print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
