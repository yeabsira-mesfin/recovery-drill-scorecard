"""Deterministic filtering, projection, pagination and aggregates."""

from collections import Counter
from datetime import datetime

from .validation import ValidationError, canonical, choice, identifier, integer, timestamp

SORTS = ("id", "observed_at")


def instant(value):
    return datetime.fromisoformat(timestamp(value).replace("Z", "+00:00"))


def filter_records(rows, tag=None, since=None, until=None, search=None):
    if tag is not None:
        identifier(tag, "tag")
    lower = instant(since) if since else None
    upper = instant(until) if until else None
    if lower and upper and lower > upper:
        raise ValidationError("since must not be later than until")
    result = []
    for row in rows:
        observed = instant(row["observed_at"])
        if tag is not None and tag not in row["tags"]:
            continue
        if lower is not None and observed < lower:
            continue
        if upper is not None and observed > upper:
            continue
        if search and search.casefold() not in canonical(row).casefold():
            continue
        result.append(row)
    return result


def page(rows, sort="id", descending=False, offset=0, limit=100):
    choice(sort, SORTS, "sort")
    integer(offset, "offset", 0)
    integer(limit, "limit", 1, 1000)
    if sort == "observed_at":
        ordered = sorted(rows, key=lambda row: (instant(row[sort]), row["id"]),
                         reverse=descending)
    else:
        ordered = sorted(rows, key=lambda row: row["id"], reverse=descending)
    selected = ordered[offset:offset + limit]
    next_offset = offset + limit if offset + limit < len(ordered) else None
    return {"total": len(rows), "offset": offset, "next_offset": next_offset,
            "records": selected}


def summary(rows):
    counts = Counter(tag for row in rows for tag in row["tags"])
    payload_keys = Counter(key for row in rows for key in row["payload"])
    times = [instant(row["observed_at"]) for row in rows]
    return {
        "records": len(rows),
        "tagged": sum(bool(row["tags"]) for row in rows),
        "tags": dict(sorted(counts.items())),
        "payload_fields": dict(sorted(payload_keys.items())),
        "earliest": timestamp(min(times).isoformat()) if times else None,
        "latest": timestamp(max(times).isoformat()) if times else None,
    }


def field_value(row, path):
    parts = path.split(".")
    value = row
    for part in parts:
        if not part or not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def project(rows, paths):
    if not paths or len(set(paths)) != len(paths):
        raise ValidationError("projection requires unique field names")
    return [{path: field_value(row, path) for path in paths} for row in rows]


def group_counts(rows, path):
    groups = Counter(canonical(field_value(row, path)) for row in rows)
    return [{"value": key, "count": count} for key, count in sorted(groups.items())]


def histogram(rows, path, width):
    integer(width, "bucket width", 1)
    buckets = Counter()
    missing = 0
    for row in rows:
        value = field_value(row, path)
        if type(value) is not int:
            missing += 1
            continue
        buckets[(value // width) * width] += 1
    return {"missing_or_noninteger": missing, "buckets": [
        {"start": start, "end_exclusive": start + width, "count": count}
        for start, count in sorted(buckets.items())
    ]}
