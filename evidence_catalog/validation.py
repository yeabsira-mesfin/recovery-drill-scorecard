"""Strict validation shared by ingestion, snapshots and HTTP queries."""

from datetime import datetime, timezone
import hashlib
import json
import math
import re


class ValidationError(ValueError):
    pass


def object_value(value, label="document"):
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be an object")
    return value


def array(value, label):
    if not isinstance(value, list):
        raise ValidationError(f"{label} must be an array")
    return value


def text(value, label, maximum=1000):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a nonempty string")
    if len(value) > maximum:
        raise ValidationError(f"{label} exceeds {maximum} characters")
    return value


def identifier(value, label="identifier"):
    value = text(value, label, 128)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValidationError(f"{label} contains unsupported characters")
    return value


def integer(value, label, minimum=None, maximum=None):
    if type(value) is not int:
        raise ValidationError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ValidationError(f"{label} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValidationError(f"{label} must be at most {maximum}")
    return value


def boolean(value, label):
    if type(value) is not bool:
        raise ValidationError(f"{label} must be a boolean")
    return value


def choice(value, allowed, label):
    if value not in allowed:
        raise ValidationError(f"{label} must be one of {', '.join(allowed)}")
    return value


def fields(value, required, optional=(), label="document"):
    object_value(value, label)
    missing = set(required) - value.keys()
    unknown = value.keys() - set(required) - set(optional)
    if missing:
        raise ValidationError(f"{label} missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ValidationError(f"{label} unknown fields: {', '.join(sorted(unknown))}")
    return value


def timestamp(value, label="timestamp"):
    text(value, label, 80)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValidationError(f"{label} must be ISO 8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"{label} must include a UTC offset")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as error:
        raise ValidationError("value is not finite JSON") from error


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def json_tree(value, label="payload", depth=0):
    if depth > 24:
        raise ValidationError(f"{label} is too deeply nested")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError(f"{label} must contain finite numbers")
        return
    if isinstance(value, list):
        for item in value:
            json_tree(item, label, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            text(key, "payload key", 128)
            json_tree(item, label, depth + 1)
        return
    raise ValidationError(f"{label} has an unsupported value")


def tags(value):
    array(value, "tags")
    if len(value) > 32:
        raise ValidationError("at most 32 tags are allowed")
    return sorted(set(identifier(item, "tag") for item in value))


def record(value, validator):
    fields(value, ("id", "tenant", "observed_at", "payload"), ("tags",))
    payload = object_value(value["payload"], "payload")
    json_tree(payload)
    validator(payload)
    if len(canonical(payload).encode("utf-8")) > 262144:
        raise ValidationError("payload exceeds 256 KiB")
    return {
        "id": identifier(value["id"], "record id"),
        "tenant": identifier(value["tenant"], "tenant"),
        "observed_at": timestamp(value["observed_at"]),
        "tags": tags(value.get("tags", [])),
        "payload": payload,
    }


def batch(values, validator):
    array(values, "records")
    if len(values) > 10000:
        raise ValidationError("a batch is limited to 10000 records")
    normalized = {}
    for index, value in enumerate(values):
        try:
            row = record(value, validator)
        except ValidationError as error:
            raise ValidationError(f"record {index + 1}: {error}") from error
        key = (row["tenant"], row["id"])
        if key in normalized and normalized[key] != row:
            raise ValidationError(f"conflicting duplicate {key}")
        normalized[key] = row
    return sorted(normalized.values(), key=lambda row: (row["tenant"], row["id"]))
