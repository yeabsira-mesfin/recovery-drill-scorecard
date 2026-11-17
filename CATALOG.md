# Evidence catalog

The application includes a working catalog for the synthetic records used by this project. It stores evidence with a tenant, stable record ID, observation time and tags. The domain validator checks each payload against the project's record schema. The decision algorithm in ASSIGNMENT.md is the remaining implementation task.

## Run the existing application

Use Python 3.10 or later. All dependencies are in the standard library. Run from the repository root.

```sh
python catalog.py sample > sample.json
python catalog.py init --db out/evidence.sqlite
python catalog.py validate sample.json
python catalog.py import --db out/evidence.sqlite sample.json
python catalog.py list --db out/evidence.sqlite --tenant demo
python catalog.py stats --db out/evidence.sqlite --tenant demo
python catalog.py report --db out/evidence.sqlite --tenant demo --format html --output out/report.html
python catalog.py serve --db out/evidence.sqlite --tenant demo
```

Open http://127.0.0.1:8765/ for the read-only viewer. Stop it with Ctrl+C. The viewer fixes the tenant at startup. It exposes the HTML report, `/records` and `/summary`. It is an offline synthetic lab and has no production authentication.

## Input envelope

The sample command prints a complete valid document. An import document has a records array. Each record has id, tenant, observed_at, payload, and optional tags. Timestamps require a UTC offset. IDs and tags use letters, digits, underscores, dots or hyphens. Payload fields depend on this project's domain.py. The catalog envelope is distinct from the decision examples' raw input schema.

Import accepts JSON, JSONL and CSV through `--format`. CSV has the exact header `id,tenant,observed_at,tags,payload`; tags and payload are JSON values encoded inside CSV cells. Exported CSV can be imported again. Human-facing report CSV additionally escapes spreadsheet formula prefixes.

Imports validate the entire batch, then commit records and audit entries in one SQLite transaction. Repeating identical normalized records is idempotent. A conflicting tenant/ID pair rolls back the batch. Import requires an initialized database. `--dry-run` validates input without opening the database and does not check conflicts with stored records.

## Query and reporting

```sh
python catalog.py list --db out/evidence.sqlite --tenant demo --tag synthetic --limit 10 --sort observed_at
python catalog.py export --db out/evidence.sqlite --tenant demo --format jsonl --output out/records.jsonl
python catalog.py select --db out/evidence.sqlite --tenant demo --field id --field payload --format csv
python catalog.py group --db out/evidence.sqlite --tenant demo --field tags
python catalog.py quality --db out/evidence.sqlite --tenant demo --as-of 2026-02-01T00:00:00Z --max-age-days 30
```

List, report, export, stats and select support tag, since, until and case-insensitive search filters. Date bounds are inclusive. List includes a total and next_offset for pagination. Export includes every matching row. Select supports dotted paths, returning null for absent fields. Histogram groups integer fields using `--field payload.FIELD --width N`. Quality reports future observations, stale records, missing required tags or fields, and repeated payloads. These are configurable lab checks.

## Updates and evidence history

`get --db PATH --tenant TENANT ID` returns the record and its checksum. Use the checksum with `retag --expected-checksum HASH --tag TAG` to avoid overwriting a concurrent tag update. Omit all tag options to clear tags. A no-op leaves audit history unchanged. Changing tags changes normalized record content, so reimporting its old envelope will produce a conflict.

```sh
python catalog.py audit --db out/evidence.sqlite --tenant demo --verify
python catalog.py snapshot --db out/evidence.sqlite --tenant demo out/before.json
python catalog.py diff out/before.json out/before.json
python catalog.py health --db out/evidence.sqlite
python catalog.py backup --db out/evidence.sqlite out/backup.sqlite
```

Audit verification checks chained checksums and the latest stored record contents within a consistent read transaction. The chain detects accidental modification; a person who can rewrite the database can also recompute it. It is not a signed or externally anchored log. A backup uses SQLite's consistent backup interface and requires a new destination.

## Portable bundles

```sh
python catalog.py bundle --db out/evidence.sqlite --tenant demo out/evidence.zip
python catalog.py inspect-bundle out/evidence.zip
python catalog.py init --db out/restored.sqlite
python catalog.py import-bundle --db out/restored.sqlite out/evidence.zip
```

Bundles contain a deterministic snapshot, CSV, HTML and a SHA-256 manifest. Inspection checks allowed members, bounds decompression, validates the snapshot and reconciles all representations. Import reads members in memory and applies the same transactional import rules. `restore-snapshot` imports a JSON snapshot using those rules. Restoring records creates a new import history; the original catalog's audit events are retained in a database backup.

## Verification

```sh
python -m unittest discover -s tests -v
python tools/count_loc.py
python acceptance.py --inventory
```

The normal unittest suite tests existing behavior. The 72 decision acceptance examples target the assignment feature and fail on this starter until that feature is implemented. Add domain-specific decision and integration tests during the assignment.
