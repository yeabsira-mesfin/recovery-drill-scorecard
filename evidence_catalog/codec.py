"""Bounded input readers and atomic output writers."""

import csv
import io
import json
import os
from pathlib import Path
import tempfile

from .validation import ValidationError, array, canonical, fields

MAX_BYTES = 16 * 1024 * 1024
COLUMNS = ("id", "tenant", "observed_at", "tags", "payload")


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value):
    raise ValidationError(f"invalid JSON number: {value}")


def parse_json(source):
    try:
        return json.loads(source, object_pairs_hook=unique_pairs,
                          parse_constant=reject_constant)
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValidationError(f"invalid JSON: {error}") from error


def read_text(path):
    with Path(path).open("rb") as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValidationError("input exceeds 16 MiB")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValidationError("input must be UTF-8") from error


def load_records(path, format_name):
    source = read_text(path)
    if format_name == "json":
        document = parse_json(source)
        if isinstance(document, dict):
            fields(document, ("records",))
            document = document["records"]
        return array(document, "records")
    if format_name == "jsonl":
        result = []
        for index, line in enumerate(source.splitlines(), 1):
            if not line.strip():
                continue
            try:
                result.append(parse_json(line))
            except ValidationError as error:
                raise ValidationError(f"line {index}: {error}") from error
        return result
    if format_name == "csv":
        return read_csv(source)
    raise ValidationError("unsupported input format")


def read_csv(source):
    reader = csv.DictReader(io.StringIO(source, newline=""), strict=True)
    if reader.fieldnames != list(COLUMNS):
        raise ValidationError("CSV header must be " + ",".join(COLUMNS))
    result = []
    try:
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValidationError(f"wrong column count at line {reader.line_num}")
            row["tags"] = parse_json(row["tags"])
            row["payload"] = parse_json(row["payload"])
            result.append(row)
    except csv.Error as error:
        raise ValidationError(f"invalid CSV: {error}") from error
    return result


def render_records(rows, format_name):
    if format_name == "json":
        return json.dumps({"records": rows}, indent=2, ensure_ascii=False,
                          sort_keys=True, allow_nan=False) + "\n"
    if format_name == "jsonl":
        return "".join(canonical(row) + "\n" for row in rows)
    if format_name == "csv":
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            encoded = dict(row)
            encoded["tags"] = canonical(row["tags"])
            encoded["payload"] = canonical(row["payload"])
            writer.writerow(encoded)
        return output.getvalue()
    raise ValidationError("unsupported output format")


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=path.parent, delete=False) as handle:
            name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        name = None
    finally:
        if name is not None:
            Path(name).unlink(missing_ok=True)
