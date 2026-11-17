"""Portable evidence packages with byte-level checksums and bounded reads."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo

from .codec import parse_json, render_records
from .reports import render_report
from .snapshots import create_snapshot, validate_snapshot
from .validation import ValidationError, canonical, fields

MEMBERS = {"snapshot.json", "records.csv", "report.html", "manifest.json"}
MAX_MEMBER_BYTES = 16 * 1024 * 1024


def bytes_digest(value):
    return hashlib.sha256(value).hexdigest()


def build_members(catalog, tenant):
    rows = catalog.records(tenant)
    snapshot = create_snapshot(catalog.project, tenant, rows, catalog.validator)
    members = {
        "snapshot.json": (canonical(snapshot) + "\n").encode("utf-8"),
        "records.csv": render_records(rows, "csv").encode("utf-8"),
        "report.html": render_report(catalog.project, tenant, rows, "html").encode("utf-8"),
    }
    manifest = {
        "version": 1,
        "project": catalog.project,
        "tenant": tenant,
        "files": {name: {"sha256": bytes_digest(data), "bytes": len(data)}
                  for name, data in sorted(members.items())},
    }
    members["manifest.json"] = (canonical(manifest) + "\n").encode("utf-8")
    if any(len(data) > MAX_MEMBER_BYTES for data in members.values()):
        raise ValidationError("bundle member exceeds 16 MiB")
    return members


def write_bundle(catalog, tenant, output):
    output = Path(output)
    members = build_members(catalog, tenant)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for name, data in sorted(members.items()):
                entry = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
                entry.compress_type = ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, data)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"bundle": str(output), "members": sorted(members)}


def read_members(path):
    members = {}
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) != len(MEMBERS) or {entry.filename for entry in entries} != MEMBERS:
                raise ValidationError("bundle has unexpected or duplicate members")
            for entry in entries:
                if entry.file_size > MAX_MEMBER_BYTES or entry.flag_bits & 1:
                    raise ValidationError("bundle is oversized or encrypted")
                file_type = (entry.external_attr >> 16) & 0o170000
                if file_type not in (0, 0o100000):
                    raise ValidationError("bundle members must be regular files")
                with archive.open(entry) as handle:
                    body = handle.read(MAX_MEMBER_BYTES + 1)
                if len(body) > MAX_MEMBER_BYTES:
                    raise ValidationError("decompressed member is too large")
                members[entry.filename] = body
    except (BadZipFile, RuntimeError, NotImplementedError) as error:
        raise ValidationError("invalid evidence bundle") from error
    return members


def inspect_bundle(path, project, validator):
    members = read_members(path)
    try:
        manifest = parse_json(members["manifest.json"].decode("utf-8"))
        snapshot = parse_json(members["snapshot.json"].decode("utf-8"))
    except UnicodeError as error:
        raise ValidationError("bundle metadata must be UTF-8") from error
    fields(manifest, ("version", "project", "tenant", "files"))
    if type(manifest["version"]) is not int or manifest["version"] != 1:
        raise ValidationError("unsupported bundle version")
    if manifest["project"] != project:
        raise ValidationError("bundle belongs to a different project")
    fields(manifest["files"], MEMBERS - {"manifest.json"})
    for name, metadata in manifest["files"].items():
        fields(metadata, ("sha256", "bytes"))
        if type(metadata["bytes"]) is not int or metadata["bytes"] != len(members[name]):
            raise ValidationError(f"bundle size mismatch: {name}")
        if metadata["sha256"] != bytes_digest(members[name]):
            raise ValidationError(f"bundle checksum mismatch: {name}")
    validated = validate_snapshot(snapshot, project, validator)
    if manifest["tenant"] != validated["tenant"]:
        raise ValidationError("bundle tenant mismatch")
    expected_csv = render_records(validated["records"], "csv").encode("utf-8")
    expected_html = render_report(project, validated["tenant"], validated["records"], "html").encode("utf-8")
    if members["records.csv"] != expected_csv or members["report.html"] != expected_html:
        raise ValidationError("bundle representations disagree")
    return validated


def import_bundle(catalog, path):
    snapshot = inspect_bundle(path, catalog.project, catalog.validator)
    return catalog.ingest(snapshot["records"])
