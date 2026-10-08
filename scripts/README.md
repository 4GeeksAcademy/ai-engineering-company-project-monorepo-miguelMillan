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

> _Spanish version: [README.es.md](./README.es.md)._
