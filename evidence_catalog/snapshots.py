"""Content-addressed snapshots and changes between validated exports."""

from .codec import atomic_write, parse_json, read_text
from .validation import ValidationError, batch, canonical, digest, fields, identifier


def create_snapshot(project, tenant, rows, validator):
    identifier(tenant, "tenant")
    normalized = batch(rows, validator)
    if any(row["tenant"] != tenant for row in normalized):
        raise ValidationError("snapshot contains another tenant")
    document = {"version": 1, "project": project, "tenant": tenant,
                "records": normalized}
    return {**document, "checksum": digest(document)}


def validate_snapshot(document, project, validator):
    fields(document, ("version", "project", "tenant", "records", "checksum"))
    if type(document["version"]) is not int or document["version"] != 1:
        raise ValidationError("unsupported snapshot version")
    if document["project"] != project:
        raise ValidationError("snapshot belongs to a different project")
    checked = create_snapshot(project, document["tenant"], document["records"], validator)
    if checked != document:
        raise ValidationError("snapshot content, ordering or checksum is invalid")
    return checked


def save_snapshot(path, project, tenant, rows, validator):
    document = create_snapshot(project, tenant, rows, validator)
    atomic_write(path, canonical(document) + "\n")
    return {"records": len(rows), "checksum": document["checksum"]}


def load_snapshot(path, project, validator):
    document = parse_json(read_text(path))
    return validate_snapshot(document, project, validator)


def changes(before, after):
    if before["tenant"] != after["tenant"] or before["project"] != after["project"]:
        raise ValidationError("snapshots must share tenant and project")
    old = {row["id"]: row for row in before["records"]}
    new = {row["id"]: row for row in after["records"]}
    rows = []
    for record_id in sorted(old.keys() | new.keys()):
        previous = old.get(record_id)
        current = new.get(record_id)
        if previous == current:
            continue
        if previous is None:
            state = "added"
        elif current is None:
            state = "removed"
        else:
            state = "changed"
        rows.append({"id": record_id, "state": state,
                     "before": previous, "after": current})
    return {"tenant": before["tenant"], "changes": rows}


def compare_files(before_path, after_path, project, validator):
    before = load_snapshot(before_path, project, validator)
    after = load_snapshot(after_path, project, validator)
    return changes(before, after)


def restore_snapshot(catalog, path):
    document = load_snapshot(path, catalog.project, catalog.validator)
    return catalog.ingest(document["records"])


def verify_audit(events, tenant):
    previous = "0" * 64
    sequence = 0
    current = {}
    failures = []
    for entry in events:
        event = entry["event"]
        if entry["seq"] <= sequence:
            failures.append({"seq": entry["seq"], "reason": "sequence"})
        if entry["previous"] != previous:
            failures.append({"seq": entry["seq"], "reason": "previous hash"})
        if event["tenant"] != tenant:
            failures.append({"seq": entry["seq"], "reason": "tenant"})
        expected = digest({"previous": entry["previous"], "event": event})
        if entry["checksum"] != expected:
            failures.append({"seq": entry["seq"], "reason": "checksum"})
        details = event["details"]
        record_id = details["id"]
        if event["action"] == "retag" and current.get(record_id) != details["before"]:
            failures.append({"seq": entry["seq"], "reason": "record predecessor"})
        if event["action"] == "import" and record_id in current:
            failures.append({"seq": entry["seq"], "reason": "duplicate import"})
        current[record_id] = details["checksum"]
        previous = entry["checksum"]
        sequence = entry["seq"]
    return {"valid": not failures, "events": len(events), "head": previous,
            "failures": failures, "record_checksums": current}


def verify_catalog(catalog, tenant):
    rows, events = catalog.verification_state(tenant)
    result = verify_audit(events, tenant)
    observed = {row["id"]: digest(row) for row in rows}
    if observed != result["record_checksums"]:
        result["valid"] = False
        result["failures"].append({"seq": None, "reason": "stored records differ from audit"})
    return result
