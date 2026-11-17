import copy
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from zipfile import ZipFile

from domain import PROJECT, example_payload, validate_payload
from evidence_catalog.bundles import import_bundle, inspect_bundle, write_bundle
from evidence_catalog.codec import atomic_write, load_records, parse_json, render_records
from evidence_catalog.query import filter_records, group_counts, histogram, page, project, summary
from evidence_catalog.quality import inspect_quality
from evidence_catalog.reports import csv_table, html_report
from evidence_catalog.snapshots import (
    compare_files, create_snapshot, restore_snapshot, save_snapshot,
    validate_snapshot, verify_audit, verify_catalog,
)
from evidence_catalog.storage import Catalog
from evidence_catalog.validation import ValidationError, batch, digest, integer, timestamp
from evidence_catalog.web import handler_for


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "test.sqlite"
        self.catalog = Catalog(self.db, validate_payload, PROJECT)
        self.catalog.initialize()
        self.row = {"id": "a", "tenant": "alpha", "observed_at": "2026-01-01T00:00:00Z",
                    "tags": ["review"], "payload": example_payload()}

    def test_idempotency_and_tenant_isolation(self):
        other = {**self.row, "tenant": "beta"}
        self.assertEqual(self.catalog.ingest([self.row, other])["inserted"], 2)
        self.assertEqual(self.catalog.ingest([self.row])["duplicates"], 1)
        self.assertEqual(len(self.catalog.records("alpha")), 1)
        self.assertEqual(len(self.catalog.audit("alpha")), 1)
        self.assertEqual(self.catalog.records("absent"), [])

    def test_transaction_rollback_after_earlier_insert(self):
        self.catalog.ingest([{**self.row, "id": "z"}])
        conflicting = {**self.row, "id": "z", "tags": ["changed"]}
        with self.assertRaisesRegex(ValidationError, "conflicting"):
            self.catalog.ingest([self.row, conflicting])
        self.assertEqual([r["id"] for r in self.catalog.records("alpha")], ["z"])
        self.assertEqual(len(self.catalog.audit("alpha")), 1)

    def test_concurrent_imports_are_idempotent(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.catalog.ingest([self.row]), range(4)))
        self.assertEqual(sum(r["inserted"] for r in results), 1)
        self.assertEqual(sum(r["duplicates"] for r in results), 3)
        self.assertTrue(verify_catalog(self.catalog, "alpha")["valid"])

    def test_concurrent_initialization(self):
        catalog = Catalog(self.root / "parallel.sqlite", validate_payload, PROJECT)
        with ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(lambda _: catalog.initialize(), range(3)))
        self.assertEqual(catalog.health()["records"], 0)

    def test_unknown_schema_is_preserved(self):
        path = self.root / "foreign.sqlite"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        with self.assertRaisesRegex(ValidationError, "unrecognized"):
            Catalog(path, validate_payload, PROJECT).initialize()
        with sqlite3.connect(path) as connection:
            self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE name='unrelated'").fetchone())

    def test_cross_project_database_rejected(self):
        with self.assertRaises(ValidationError):
            Catalog(self.db, validate_payload, "other-project").health()

    def test_retag_compare_and_swap(self):
        self.catalog.ingest([self.row])
        old = self.catalog.get("alpha", "a")
        updated = self.catalog.retag("alpha", "a", ["done"], digest(old))
        self.assertTrue(updated["changed"])
        with self.assertRaises(ValidationError):
            self.catalog.retag("alpha", "a", ["stale"], digest(old))
        self.assertEqual(self.catalog.get("alpha", "a")["tags"], ["done"])
        self.assertEqual(len(self.catalog.audit("alpha")), 2)
        self.assertTrue(verify_catalog(self.catalog, "alpha")["valid"])

    def test_retag_noop_and_missing(self):
        self.catalog.ingest([self.row])
        row = self.catalog.get("alpha", "a")
        self.assertFalse(self.catalog.retag("alpha", "a", ["review"], digest(row))["changed"])
        with self.assertRaises(ValidationError):
            self.catalog.retag("beta", "a", [], digest(row))
        self.assertEqual(len(self.catalog.audit("alpha")), 1)

    def test_audit_detects_changed_event(self):
        self.catalog.ingest([self.row])
        events = self.catalog.audit("alpha")
        events[0]["event"]["action"] = "altered"
        self.assertFalse(verify_audit(events, "alpha")["valid"])

    def test_audit_detects_removed_record(self):
        self.catalog.ingest([self.row])
        with sqlite3.connect(self.db) as connection:
            connection.execute("DELETE FROM records")
        self.assertFalse(verify_catalog(self.catalog, "alpha")["valid"])

    def test_consistent_backup_and_existing_destination(self):
        self.catalog.ingest([self.row])
        backup = self.root / "copy.sqlite"
        self.catalog.backup(backup)
        copied = Catalog(backup, validate_payload, PROJECT)
        self.assertEqual(copied.records("alpha"), self.catalog.records("alpha"))
        self.assertTrue(verify_catalog(copied, "alpha")["valid"])
        with self.assertRaises(ValidationError):
            self.catalog.backup(backup)

    def test_codec_round_trips(self):
        rows = batch([self.row], validate_payload)
        for format_name in ("json", "jsonl", "csv"):
            with self.subTest(format=format_name):
                path = self.root / ("input." + format_name)
                atomic_write(path, render_records(rows, format_name))
                self.assertEqual(load_records(path, format_name), rows)

    def test_invalid_csv_columns(self):
        path = self.root / "bad.csv"
        path.write_text("id,tenant\na,alpha\n", encoding="utf-8")
        with self.assertRaises(ValidationError):
            load_records(path, "csv")

    def test_duplicate_json_keys_and_nan(self):
        for source in ('{"records":[],"records":[]}', '{"value":NaN}'):
            with self.assertRaises(ValidationError):
                parse_json(source)

    def test_batch_rejects_conflicting_duplicates(self):
        conflict = {**self.row, "tags": ["different"]}
        with self.assertRaises(ValidationError):
            batch([self.row, conflict], validate_payload)
        self.assertEqual(len(batch([self.row, self.row], validate_payload)), 1)

    def test_strict_fields_and_types(self):
        for bad in ({**self.row, "tenant": "../other"},
                    {**self.row, "observed_at": "2026-01-01"},
                    {**self.row, "tags": "review"},
                    {**self.row, "extra": 1},
                    {**self.row, "payload": {}}):
            with self.assertRaises(ValidationError):
                batch([bad], validate_payload)
        with self.assertRaises(ValidationError):
            integer(True, "count")

    def test_timestamps_and_tag_normalization(self):
        row = {**self.row, "observed_at": "2025-12-31T19:00:00-05:00",
               "tags": ["z", "review", "z"]}
        normalized = batch([row], validate_payload)[0]
        self.assertEqual(normalized["observed_at"], timestamp(self.row["observed_at"]))
        self.assertEqual(normalized["tags"], ["review", "z"])

    def test_query_boundaries_and_pagination(self):
        rows = batch([self.row, {**self.row, "id": "b", "tags": []}], validate_payload)
        self.assertEqual(len(filter_records(rows, since="2026-01-01T00:00:00Z", until="2026-01-01T00:00:00Z")), 2)
        self.assertEqual(len(filter_records(rows, tag="review")), 1)
        self.assertEqual(page(rows, limit=1)["next_offset"], 1)
        self.assertEqual(page(rows, descending=True, limit=1)["records"][0]["id"], "b")
        self.assertEqual(page(rows, offset=100)["records"], [])
        with self.assertRaises(ValidationError):
            page(rows, limit=0)
        with self.assertRaises(ValidationError):
            filter_records(rows, since="2026-01-02T00:00:00Z", until="2026-01-01T00:00:00Z")

    def test_statistics_projection_and_histogram(self):
        rows = [{**self.row, "payload": {"measurement": -1}},
                {**self.row, "id": "b", "payload": {"measurement": 5}}]
        self.assertEqual(summary(rows)["records"], 2)
        self.assertEqual(project(rows, ["payload.missing"]), [{"payload.missing": None}] * 2)
        self.assertEqual(group_counts(rows, "tenant")[0]["count"], 2)
        bins = histogram(rows, "payload.measurement", 5)["buckets"]
        self.assertEqual([r["start"] for r in bins], [-5, 5])
        self.assertEqual(summary([])["earliest"], None)

    def test_quality_boundaries_and_duplicate_payload(self):
        rows = [self.row, {**self.row, "id": "b"}]
        report = inspect_quality(rows, "2026-01-31T00:00:00Z", 30)
        self.assertEqual(report["affected_records"], 0)
        self.assertEqual(report["duplicate_payload_groups"], [["a", "b"]])
        report = inspect_quality(rows, "2026-02-01T00:00:00Z", 30, ["required"], ["payload.missing"])
        self.assertEqual(report["rule_counts"]["stale-observation"], 2)

    def test_snapshot_checksums_isolation_and_restore(self):
        self.catalog.ingest([self.row])
        path = self.root / "snapshot.json"
        rows = self.catalog.records("alpha")
        save_snapshot(path, PROJECT, "alpha", rows, validate_payload)
        self.assertEqual(restore_snapshot(self.catalog, path)["duplicates"], 1)
        document = json.loads(path.read_text())
        document["records"][0]["tags"] = ["tampered"]
        with self.assertRaises(ValidationError):
            validate_snapshot(document, PROJECT, validate_payload)
        with self.assertRaises(ValidationError):
            create_snapshot(PROJECT, "beta", rows, validate_payload)

    def test_snapshot_diff(self):
        before = self.root / "before.json"
        after = self.root / "after.json"
        save_snapshot(before, PROJECT, "alpha", [self.row], validate_payload)
        save_snapshot(after, PROJECT, "alpha", [{**self.row, "tags": []}], validate_payload)
        difference = compare_files(before, after, PROJECT, validate_payload)
        self.assertEqual(difference["changes"][0]["state"], "changed")

    def test_html_escaping_and_csv_formula_protection(self):
        row = {**self.row, "payload": {"note": "<script>alert(1)</script>"}}
        html = html_report("<title>", "demo", [row])
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("'=1+1", csv_table([{"x": "=1+1"}], ["x"]))

    def test_bundle_roundtrip_and_determinism(self):
        self.catalog.ingest([self.row])
        first = self.root / "one.zip"
        second = self.root / "two.zip"
        write_bundle(self.catalog, "alpha", first)
        write_bundle(self.catalog, "alpha", second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(inspect_bundle(first, PROJECT, validate_payload)["tenant"], "alpha")
        self.assertEqual(import_bundle(self.catalog, first)["duplicates"], 1)

    def test_bundle_rejects_tampering_and_extra_paths(self):
        self.catalog.ingest([self.row])
        good = self.root / "good.zip"
        write_bundle(self.catalog, "alpha", good)
        with ZipFile(good) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        for name in ("records.csv", "../outside"):
            bad = self.root / "bad.zip"
            changed = {**members, name: b"tampered"}
            with ZipFile(bad, "w") as archive:
                for member, content in changed.items():
                    archive.writestr(member, content)
            with self.assertRaises(ValidationError):
                inspect_bundle(bad, PROJECT, validate_payload)

    def test_loopback_routes_and_tenant_scope(self):
        self.catalog.ingest([self.row, {**self.row, "id": "secret", "tenant": "beta"}])
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.catalog, "alpha"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(base + "/records") as response:
                data = json.load(response)
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(data["total"], 1)
            self.assertEqual(data["records"][0]["tenant"], "alpha")
            with urlopen(base + "/") as response:
                self.assertIn(b"Evidence records", response.read())
            with self.assertRaises(HTTPError) as failure:
                urlopen(base + "/records?tenant=beta")
            self.assertEqual(failure.exception.code, 400)
            with self.assertRaises(HTTPError) as failure:
                urlopen(base + "/unknown")
            self.assertEqual(failure.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_cli_import_export_and_snapshot(self):
        script = str(Path(__file__).resolve().parents[1] / "catalog.py")
        def run(*args):
            return subprocess.run([sys.executable, script, *args], capture_output=True,
                                  text=True, cwd=self.root, timeout=15)
        sample = run("sample")
        self.assertEqual(sample.returncode, 0, sample.stderr)
        source = self.root / "input.json"
        source.write_text(sample.stdout, encoding="utf-8")
        result = run("import", "--db", str(self.db), str(source))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run("export", "--db", str(self.db), "--tenant", "demo")
        self.assertEqual(len(json.loads(result.stdout)["records"]), 1)
        result = run("audit", "--db", str(self.db), "--tenant", "demo", "--verify")
        self.assertTrue(json.loads(result.stdout)["valid"])
        result = run("get", "--db", str(self.db), "--tenant", "missing", "sample-01")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
