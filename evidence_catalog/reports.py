"""Escaped HTML and spreadsheet-safe tabular summaries."""

import csv
from html import escape
import io
import json

from .query import project, summary
from .validation import ValidationError, canonical


def spreadsheet_cell(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = canonical(value)
    value = str(value)
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def csv_table(rows, columns):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(spreadsheet_cell(row.get(column)) for column in columns)
    return stream.getvalue()


def html_table(rows, columns):
    cells = "".join("<th scope='col'>" + escape(column) + "</th>" for column in columns)
    body = []
    for row in rows:
        rendered = []
        for column in columns:
            value = row.get(column)
            if isinstance(value, (dict, list)):
                value = canonical(value)
            rendered.append("<td>" + escape(str(value if value is not None else "")) + "</td>")
        body.append("<tr>" + "".join(rendered) + "</tr>")
    return "<table><thead><tr>" + cells + "</tr></thead><tbody>" + "".join(body) + "</tbody></table>"


def html_report(title, tenant, rows):
    totals = summary(rows)
    table_rows = project(rows, ["id", "observed_at", "tags", "payload"])
    table = html_table(table_rows, ["id", "observed_at", "tags", "payload"])
    header = escape(title + " / " + tenant)
    return (
        "<!doctype html><html lang='en'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>" + header + "</title>"
        "<style>body{font:16px system-ui;margin:2rem;line-height:1.5}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #aaa;"
        "padding:.5rem;text-align:left;overflow-wrap:anywhere}"
        "main{max-width:90rem;margin:auto}section{overflow-x:auto}</style>"
        "<main><h1>" + header + "</h1><p>Records: " + str(totals["records"]) +
        "</p><p>Tagged records: " + str(totals["tagged"]) +
        "</p><section aria-label='Evidence records'>" + table + "</section></main></html>\n"
    )


def render_report(title, tenant, rows, format_name):
    if format_name == "html":
        return html_report(title, tenant, rows)
    if format_name == "json":
        return json.dumps({"tenant": tenant, "summary": summary(rows), "records": rows},
                          indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    if format_name == "csv":
        columns = ["id", "observed_at", "tags", "payload"]
        return csv_table(project(rows, columns), columns)
    raise ValidationError("unsupported report format")
