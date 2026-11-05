# SSIS Conversion & Assessment Accelerator

Generate Databricks Spark SQL/Python artifacts and report what still needs implementation or verification. Generated artifacts do **not** certify legacy equivalence. Runtime behavioral match is reported as `NOT_MEASURED`, with no invented accuracy percentage.

## Install and run

```sh
python -m pip install -e '.[dev]'
streamlit run frontend/app.py
```

For AI-assisted generation, install `.[ai]` and configure the selected provider. AI is disabled by default. No Databricks workloads are executed or deployed by this tool.

```sh
python -m ssis_migration.migrate \
  --source examples/wwi/DailyETLMain.dtsx \
  --output ./assessment-output \
  --base-path . \
  --catalog dev_catalog \
  --layer silver \
  --procedure /path/to/procedures-a.sql \
  --procedure /path/to/procedures-b.txt \
  --ddl /path/to/tables.sql
```

Use `--dry-run` to parse, resolve, and write the plan/diagnostics without final notebook generation or AI calls. Use `--strict-mode` to block notebook output for error findings. In normal mode, incomplete operations are replaced by explicit runtime errors, and notebooks with unresolved blockers have a fail-closed execution gate.

CLI exit status is nonzero for technical failures and, in strict mode, packages requiring review. The Python API always returns a structured report for package-level failures. Invalid top-level configuration raises an exception. `--execute` is unsupported.

## Inputs

- One mandatory `.dtsx`, or a directory of packages through the CLI/API.
- Multiple optional procedure files (`--procedure`, repeatable), and DDL files (`--ddl`, repeatable).
- Optional connection mapping JSON with a `mappings` list. Referenced metadata files are resolved against `--base-path`.
- Logical layer: Bronze, Silver, Gold, Multi-layer, unspecified, or custom text. No LLM is used to select it.
- Optional target-schema context and business description. **Physical schema rewriting and per-output multi-layer mapping remain review requirements.**

The existing `procedures_path=` and `ddl_path=` API arguments are retained; new callers can use `procedures_paths=[...]` and `ddl_paths=[...]`.

All supporting files are consumed. A mixed procedure/DDL file can contribute to both collections. Conflicting definitions are rejected instead of silently choosing a source. Identical repeated definitions are accepted. Unqualified calls use the configurable `default_schema` hint (default `dbo`); this is not a claim to reproduce every SQL Server principal-specific resolution rule.

## Outputs and interpretation

`migration_report.json` is the run index. Each package/input version contains:

- `migration_manifest.json`: content hashes, engine/configuration identity, and artifact paths.
- `package_ir.json`, `analysis.json`, `migration_plan.json`, `source_inventory.json`.
- `operations.json`: original generation output for diagnosis, including rejected content.
- `validation_results.json`, `assessment.json`, and a Markdown review report.
- A `_Migration.ipynb` notebook when output is permitted. The filename suffix is retained for compatibility; it does not indicate certified migration.

Statuses include `REVIEW_REQUIRED`, `ARTIFACTS_GENERATED`, `ANALYZED`, `PARTIAL_FAILURE`, and `FAILED`. There is no unconditional `MIGRATED` result.

Operation coverage is the percentage of generated operations passing native-code static checks. It is **not** task coverage, runtime validation, or behavioral accuracy. All source tasks/components have a disposition; unsupported standalone work is retained as a review step rather than disappearing.

Content-addressed output versions change with source/dependencies, referenced metadata, configuration, and engine code. Same-input findings retain stable IDs. A notebook with `metadata.human_reviewed = true` or a cell containing `# HUMAN_REVIEWED: true` is preserved; unreadable existing notebooks are also protected. Unmarked edits are not automatically recognized. These policies concern generated artifacts, not data-pipeline rerun safety.

## Current boundary

Existing warehouse logical-load rules still use sequence/task-name conventions and WWI control tables. They now produce explicit review findings. Complex routing, failure/completion semantics, generic standalone translation, and per-output schema mapping are **not** silently claimed as implemented. Full graph-aware translation, runtime comparisons, Volume browsing, and optional reference guidance are subsequent work; pipeline and load strategies are not selected by this change.

Static validation rejects visible Spark UDF/Pandas UDF/UDTF definitions and registration, selected custom row APIs, dynamic Python execution, unresolved SQL parameters, legacy EXEC passthrough, incomplete bodies, and unbounded driver collections. Ordinary Python helpers and explicitly bounded one-row retrieval remain supported. This is a conversion-quality check, not a sandbox for arbitrary Python.

See [the assessment contract](docs/assessment-contract.md) for details and follow-on acceptance criteria.

## Tests

```sh
pytest -q --ignore=tests/agent/test_live_llm_integration.py
```

The live integration module requires a separately configured Databricks endpoint; do not use it for offline validation. Source code and examples in this repository are the only inputs used by automated tests. Confidential third-party tool archives are not included.
