"""Record freshness, completeness, content duplication and baseline drift."""

from collections import Counter, defaultdict
from datetime import timedelta

from .query import field_value, instant
from .validation import identifier, integer, digest


def inspect_quality(rows, as_of, max_age_days, required_tags=(), required_fields=()):
    cutoff = instant(as_of)
    integer(max_age_days, "max_age_days", 0, 365000)
    for tag in required_tags:
        identifier(tag, "required tag")
    earliest = cutoff - timedelta(days=max_age_days)
    findings = []
    contents = defaultdict(list)
    for row in rows:
        observed = instant(row["observed_at"])
        if observed > cutoff:
            findings.append({"id": row["id"], "rule": "future-observation", "detail": row["observed_at"]})
        elif observed < earliest:
            findings.append({"id": row["id"], "rule": "stale-observation", "detail": row["observed_at"]})
        for tag in sorted(set(required_tags) - set(row["tags"])):
            findings.append({"id": row["id"], "rule": "missing-tag", "detail": tag})
        for path in sorted(set(required_fields)):
            if field_value(row, path) is None:
                findings.append({"id": row["id"], "rule": "missing-field", "detail": path})
        contents[digest(row["payload"])].append(row["id"])
    duplicates = [sorted(ids) for ids in contents.values() if len(ids) > 1]
    findings.sort(key=lambda item: (item["id"], item["rule"], item["detail"]))
    rules = Counter(item["rule"] for item in findings)
    affected = {item["id"] for item in findings}
    return {
        "records": len(rows),
        "affected_records": len(affected),
        "passing_records": len(rows) - len(affected),
        "rule_counts": dict(sorted(rules.items())),
        "findings": findings,
        "duplicate_payload_groups": sorted(duplicates),
    }
