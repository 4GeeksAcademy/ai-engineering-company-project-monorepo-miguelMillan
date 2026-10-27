# `scripts` folder

This folder contains **helper scripts** for the monorepo: development automation, maintenance utilities, repetitive tasks (setup, lint, migrations, data generation, etc.), and internal tooling.

- **Main purpose**: group support tools that do not belong to a specific app, agent, or pipeline but make the team’s work easier.
- **Recommendation**: document each script (what it does, parameters, requirements, usage examples) and keep them reproducible (and safe) across environments.

## Local Incident Analysis

Requires Python 3.10 or later and uses only the standard library. From the monorepo root:

```bash
python3 scripts/analyze.py incidents-trackflow.csv
```

The CLI validates the fields and catalogs in `CONTEXT.md`, excludes invalid records from metrics, reports their reasons without exposing emails, and prompts before exporting `results.csv`. When the input is named `incidents-trackflow.csv`, it compares results with the reference figures in the context and exits on discrepancies.

## Historical Incident Seed

From the monorepo root, **stop the API first** and run:

```bash
uv sync --project services/api
uv run --project services/api python scripts/seed_incidents.py
```

Defaults to `scripts/incidents-trackflow.csv` and the API's TinyDB path (`SUPPLIERS_DB_PATH` or `services/api/data/suppliers.json`). Only the `incidents` table is modified. Do not run concurrent API/seed writers against the same TinyDB file.

```bash
uv run --project services/api python scripts/seed_incidents.py path/to/input.csv --db /tmp/trackflow-incidents.json
```

The seed and analyzer share `packages/shared/incident_validation.py`. Invalid rows are rejected and their row numbers/reasons are reported at the end without printing emails or row contents. The briefing's status/category/country mappings are applied before model validation and insertion. Every incident has `origin="customer"`; descriptions are preserved verbatim, titles use the trimmed first 120 characters, and both timestamps start at midnight UTC on the CSV date.

Stable UUID5 IDs derived from `incident_id` provide persistent idempotency without storing the CSV identifier in the model. If missing, `title + created_at` is used instead. Present but malformed identifiers remain invalid; the original analyzer still requires identifiers. Existing records are skipped, never overwritten. Verification against a temporary database inserted 95 records and rejected 5 rows on the first run, then inserted 0 and skipped 95 on the second. Malformed headers exit with code 2 before insertion.

> _Spanish version: [README.es.md](./README.es.md)._
