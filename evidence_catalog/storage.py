"""Transactional, tenant-scoped SQLite evidence storage."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid

from .validation import ValidationError, batch, canonical, digest, identifier

SCHEMA = (
    "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE records (tenant TEXT NOT NULL, id TEXT NOT NULL, "
    "observed_at TEXT NOT NULL, body TEXT NOT NULL, checksum TEXT NOT NULL, "
    "PRIMARY KEY (tenant, id))",
    "CREATE TABLE audit (seq INTEGER PRIMARY KEY, tenant TEXT NOT NULL, "
    "body TEXT NOT NULL, previous TEXT NOT NULL, checksum TEXT NOT NULL)",
    "CREATE INDEX record_time ON records (tenant, observed_at, id)",
    "CREATE INDEX audit_tenant ON audit (tenant, seq)",
)


class Catalog:
    def __init__(self, path, validator, project):
        self.path = Path(path)
        self.validator = validator
        self.project = project

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                if tables:
                    raise ValidationError("database contains an unrecognized schema")
                for statement in SCHEMA:
                    connection.execute(statement)
                connection.execute("INSERT INTO metadata VALUES (?, ?)",
                                   ("project", self.project))
                connection.execute("PRAGMA user_version=1")
            self._check(connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _check(self, connection):
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != 1:
            raise ValidationError(f"unsupported database version {version}")
        row = connection.execute(
            "SELECT value FROM metadata WHERE key='project'"
        ).fetchone()
        if row is None or row[0] != self.project:
            raise ValidationError("database belongs to a different project")

    @contextmanager
    def connect(self, write=False):
        mode = "rw" if write else "ro"
        uri = self.path.resolve().as_uri() + "?mode=" + mode
        connection = sqlite3.connect(uri, uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            self._check(connection)
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _append_audit(self, connection, tenant, action, details):
        previous = connection.execute(
            "SELECT checksum FROM audit WHERE tenant=? ORDER BY seq DESC LIMIT 1",
            (tenant,),
        ).fetchone()
        previous_hash = previous[0] if previous else "0" * 64
        event = {
            "event_id": uuid.uuid4().hex,
            "at": datetime.now(timezone.utc).isoformat(),
            "tenant": tenant,
            "action": action,
            "details": details,
        }
        checksum = digest({"previous": previous_hash, "event": event})
        connection.execute(
            "INSERT INTO audit (tenant, body, previous, checksum) VALUES (?, ?, ?, ?)",
            (tenant, canonical(event), previous_hash, checksum),
        )

    def ingest(self, values):
        rows = batch(values, self.validator)
        inserted = 0
        duplicates = 0
        with self.connect(write=True) as connection:
            for row in rows:
                checksum = digest(row)
                old = connection.execute(
                    "SELECT checksum, body FROM records WHERE tenant=? AND id=?",
                    (row["tenant"], row["id"]),
                ).fetchone()
                if old is not None:
                    if old["checksum"] != checksum or old["body"] != canonical(row):
                        raise ValidationError(f"conflicting record {row['tenant']}/{row['id']}")
                    duplicates += 1
                    continue
                connection.execute(
                    "INSERT INTO records VALUES (?, ?, ?, ?, ?)",
                    (row["tenant"], row["id"], row["observed_at"], canonical(row), checksum),
                )
                self._append_audit(connection, row["tenant"], "import",
                                   {"id": row["id"], "checksum": checksum})
                inserted += 1
        return {"inserted": inserted, "duplicates": duplicates}

    def records(self, tenant):
        identifier(tenant, "tenant")
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT body FROM records WHERE tenant=? ORDER BY id", (tenant,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get(self, tenant, record_id):
        identifier(tenant, "tenant")
        identifier(record_id, "record id")
        with self.connect() as connection:
            row = connection.execute(
                "SELECT body FROM records WHERE tenant=? AND id=?", (tenant, record_id)
            ).fetchone()
        if row is None:
            raise ValidationError("record not found")
        return json.loads(row[0])

    def retag(self, tenant, record_id, new_tags, expected_checksum):
        from .validation import tags

        identifier(tenant, "tenant")
        identifier(record_id, "record id")
        normalized = tags(new_tags)
        with self.connect(write=True) as connection:
            old = connection.execute(
                "SELECT body, checksum FROM records WHERE tenant=? AND id=?",
                (tenant, record_id),
            ).fetchone()
            if old is None:
                raise ValidationError("record not found")
            if old["checksum"] != expected_checksum:
                raise ValidationError("record changed; refresh its checksum")
            row = json.loads(old["body"])
            if row["tags"] == normalized:
                return {"changed": False, "checksum": old["checksum"]}
            row["tags"] = normalized
            checksum = digest(row)
            connection.execute(
                "UPDATE records SET body=?, checksum=? WHERE tenant=? AND id=?",
                (canonical(row), checksum, tenant, record_id),
            )
            self._append_audit(connection, tenant, "retag", {
                "id": record_id, "before": old["checksum"], "checksum": checksum,
            })
        return {"changed": True, "checksum": checksum}

    def audit(self, tenant):
        identifier(tenant, "tenant")
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT seq, body, previous, checksum FROM audit WHERE tenant=? ORDER BY seq",
                (tenant,),
            ).fetchall()
        return [{"seq": row["seq"], "event": json.loads(row["body"]),
                 "previous": row["previous"], "checksum": row["checksum"]} for row in rows]

    def health(self):
        with self.connect() as connection:
            status = connection.execute("PRAGMA integrity_check").fetchone()[0]
            count = connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            tenants = connection.execute("SELECT COUNT(DISTINCT tenant) FROM records").fetchone()[0]
        return {"sqlite": status, "records": count, "tenants": tenants, "version": 1}

    def backup(self, destination):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as error:
            raise ValidationError("backup destination already exists") from error
        os.close(descriptor)
        target = sqlite3.connect(destination)
        try:
            with self.connect() as source:
                source.backup(target)
        except BaseException:
            target.close()
            destination.unlink(missing_ok=True)
            raise
        finally:
            target.close()
        return {"backup": str(destination)}

    def verification_state(self, tenant):
        identifier(tenant, "tenant")
        with self.connect() as connection:
            stored = connection.execute(
                "SELECT body FROM records WHERE tenant=? ORDER BY id", (tenant,)
            ).fetchall()
            events = connection.execute(
                "SELECT seq, body, previous, checksum FROM audit WHERE tenant=? ORDER BY seq",
                (tenant,),
            ).fetchall()
        records = [json.loads(row[0]) for row in stored]
        audit = [{"seq": row["seq"], "event": json.loads(row["body"]),
                  "previous": row["previous"], "checksum": row["checksum"]} for row in events]
        return records, audit
