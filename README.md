# SSIS Conversion & Assessment Accelerator

Generate Databricks Spark SQL/Python artifacts and report what still needs implementation or verification. Generated artifacts do **not** certify legacy equivalence. Runtime behavioral match is reported as `NOT_MEASURED`, with no invented accuracy percentage.

## Install and run

```sh
python -m pip install -e '.[dev]'
streamlit run frontend/app.py
```

For AI-assisted **legacy-mode** generation, install `.[ai]` and configure the selected provider. AI is disabled by default. The source-graph planner uses deterministic native translation and explicit blockers for unsupported work. No Databricks workloads are executed or deployed by this tool.

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
- Explicit `--object-mappings objects.json` maps legacy objects to fully qualified Databricks objects. Scoped keys use `connection_reference|schema.object`; unscoped keys deliberately apply across connections.
- Optional `--dependency-scopes scopes.json` assigns procedure/DDL file names to connection references. Ambiguous definitions are retained as findings rather than broadcast to every database.
- Optional target-schema context and business description. Context never silently rewrites object names. Multi-layer output mappings include their own `layer`.
- The default `--planner-mode graph` follows source task/port identities. `--planner-mode legacy` retains the previous warehouse-specific rules for comparison.

The existing `procedures_path=` and `ddl_path=` API arguments are retained; new callers can use `procedures_paths=[...]` and `ddl_paths=[...]`.

All supporting files are consumed. A mixed procedure/DDL file can contribute to both collections. Conflicting definitions are rejected instead of silently choosing a source. Identical repeated definitions are accepted. Unqualified calls use the configurable `default_schema` hint (default `dbo`); this is not a claim to reproduce every SQL Server principal-specific resolution rule.

## Outputs and interpretation

`migration_report.json` is the run index. Each package/input version contains:

- `migration_manifest.json`: content hashes, engine/configuration identity, and artifact paths.
- `package_ir.json`, `analysis.json`, `migration_plan.json`, `source_inventory.json`.
- `task_graph.json`, `dataflow_graphs.json`, and `dependency_inventory.json` preserve identities, scopes, graph edges, and definition provenance.
- `verification_plan.json` lists required comparisons. It contains no executed comparison results.
- `operations.json`: original generation output for diagnosis, including rejected content.
- `validation_results.json`, `assessment.json`, and a Markdown review report.
- A `_Migration.ipynb` notebook when output is permitted. The filename suffix is retained for compatibility; it does not indicate certified migration.

Statuses include `REVIEW_REQUIRED`, `ARTIFACTS_GENERATED`, `ANALYZED`, `PARTIAL_FAILURE`, and `FAILED`. There is no unconditional `MIGRATED` result.

Operation coverage is the percentage of generated operations passing native-code static checks. It is **not** task coverage, runtime validation, or behavioral accuracy. All source tasks/components have a disposition; unsupported standalone work is retained as a review step rather than disappearing.

Content-addressed output versions change with source/dependencies, referenced metadata, configuration, and engine code. Same-input findings retain stable IDs. A notebook with `metadata.human_reviewed = true` or a cell containing `# HUMAN_REVIEWED: true` is preserved; unreadable existing notebooks are also protected. Unmarked edits are not automatically recognized. These policies concern generated artifacts, not data-pipeline rerun safety.

## Current boundary

The source-graph planner supports standalone relational SQL, explicit table mappings, sequence boundaries, success-AND precedence, static mapped OLE DB sources, Multicast, a restricted Derived Column expression subset, and ordered Conditional Split outputs. It uses exact port IDs and never flattens a multi-source graph into the first source.

Data-flow destination projections are previews. Their writes remain blocked by `LOAD_STRATEGY_REQUIRED` until load semantics are agreed. Explicit single-statement DML already present in an Execute SQL Task can be translated; that preserves supplied source behavior rather than choosing a new load strategy.

Loops, Script Tasks, joins/lookups/aggregates, procedural SQL, parameter/result bindings, row-error redirection, event handlers, checkpoints, forced results, cycles, and conditional/failure/completion/OR task constraints remain explicit blockers. Independent task branches require a concurrency/failure-isolation decision before executable notebook acceptance. The optional legacy planner still uses warehouse/name/control-table conventions and reports those assumptions.

No deployment, runtime equivalence measurement, Volume browsing, or new pipeline/load strategy is implied. See [the source-graph guide](docs/source-graph.md) for mapping examples, supported subsets, and validation steps.

Static validation rejects visible Spark UDF/Pandas UDF/UDTF definitions and registration, selected custom row APIs, dynamic Python execution, unresolved SQL parameters, legacy EXEC passthrough, incomplete bodies, and unbounded driver collections. Ordinary Python helpers and explicitly bounded one-row retrieval remain supported. This is a conversion-quality check, not a sandbox for arbitrary Python.

See [the assessment contract](docs/assessment-contract.md) for details and follow-on acceptance criteria.

## Tests

```sh
pytest -q --ignore=tests/agent/test_live_llm_integration.py
```

The live integration module requires a separately configured Databricks endpoint; do not use it for offline validation. Source code and examples in this repository are the only inputs used by automated tests. Confidential third-party tool archives are not included.
