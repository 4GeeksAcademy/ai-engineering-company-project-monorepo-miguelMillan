# `mock_data/pipelines` folder

This folder groups **all data pipelines in the monorepo** related to the company: ingestion, ETL/ELT, cleaning, transformation, and loading into analytical or production systems.

Each subfolder or file under `data/pipelines/` should represent **one pipeline or job set** (for example `sales-etl`, `telemetry-stream`, `customer-segmentation`) and include the required configuration (scripts, orchestration, connectors, schemas, etc.).

- **Main purpose**: consolidate in one place the data movement and transformation logic that powers the company’s applications and analytics.
- **Recommendation**: document pipelines as you add them—their goal, data sources and sinks, dependencies, and how to run them in development, testing, and production.

> _Spanish version: [README.es.md](./README.es.md)._

The TrackFlow weekly pipeline uses extraction, transformation and load subflows,
plus an optional evaluation-export subflow. Transformation tasks live in
[`../process/weekly_performance.py`](../process/weekly_performance.py).
See the [execution and testing guide](README.es.md#pipeline-semanal-de-trackflow)
for explicit input/output contracts, installation and CLI commands.
