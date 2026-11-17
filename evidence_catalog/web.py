"""A loopback-only viewer with read-only, tenant-scoped routes."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sqlite3
from urllib.parse import parse_qs, urlsplit

from .query import filter_records, page, summary
from .reports import html_report
from .validation import ValidationError, identifier


def one(parameters, name, default=None):
    values = parameters.get(name)
    if values is None:
        return default
    if len(values) != 1:
        raise ValidationError(f"{name} must occur once")
    return values[0]


def handler_for(catalog, tenant):
    identifier(tenant, "tenant")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format_string, *args):
            pass

        def send_body(self, status, body, media="application/json"):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", media + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            try:
                self.route()
            except (ValidationError, ValueError) as error:
                self.send_body(400, json.dumps({"error": str(error)}))
            except (sqlite3.Error, OSError):
                self.send_body(503, json.dumps({"error": "catalog unavailable"}))

        def route(self):
            target = urlsplit(self.path)
            if target.path not in ("/", "/records", "/summary"):
                self.send_body(404, json.dumps({"error": "route not found"}))
                return
            parameters = parse_qs(target.query, keep_blank_values=True, max_num_fields=12)
            allowed = {"tag", "since", "until", "search", "sort", "offset", "limit"}
            if parameters.keys() - allowed:
                raise ValidationError("unknown query parameter")
            rows = filter_records(catalog.records(tenant),
                                  tag=one(parameters, "tag"),
                                  since=one(parameters, "since"),
                                  until=one(parameters, "until"),
                                  search=one(parameters, "search"))
            if target.path == "/":
                self.send_body(200, html_report(catalog.project, tenant, rows), "text/html")
                return
            if target.path == "/summary":
                result = summary(rows)
            else:
                result = page(rows, sort=one(parameters, "sort", "id"),
                              offset=int(one(parameters, "offset", "0")),
                              limit=int(one(parameters, "limit", "100")))
            self.send_body(200, json.dumps(result, ensure_ascii=False, allow_nan=False))

    return Handler


def serve(catalog, tenant, port):
    catalog.records(tenant)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(catalog, tenant))
    print(f"http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
