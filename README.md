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

Use `--dry-run` to parse, resolve, and write the plan/diagnostics without final notebook generation or AI calls. Graph mode writes review drafts even with `--strict-mode`: named pending sections retain incomplete work, and unresolved errors block execution before data operations. Strict CLI still exits nonzero when review is required. Legacy strict mode continues to withhold incomplete notebooks.

CLI exit status is nonzero for technical failures and, in strict mode, packages requiring review. The Python API always returns a structured report for package-level failures. Invalid top-level configuration raises an exception. `--execute` is unsupported.

## Inputs

- One mandatory `.dtsx`, or a directory of packages through the CLI/API.
- Multiple optional procedure files (`--procedure`, repeatable), and DDL files (`--ddl`, repeatable).
- Optional connection mapping JSON with a `mappings` list. Referenced metadata files are resolved against `--base-path`.
- Logical layer: Bronze, Silver, Gold, Multi-layer, unspecified, or custom text. No LLM is used to select it.
- Optional `--object-mappings objects.json` overrides default naming with fully qualified Databricks objects. Scoped keys use `connection_reference|schema.object`; unscoped keys deliberately apply across connections.
- Optional `--dependency-scopes scopes.json` assigns procedure/DDL file names to connection references. Ambiguous definitions are retained as findings rather than broadcast to every database.
- Optional `--target-schema` and business description. With a schema, graph mode proposes `catalog.schema.lowercase_table` names and records them for review. Without it, unresolved targets remain placeholders. Different legacy identities proposing one target produce a collision finding. Multi-layer output overrides include their own `layer`.
- The default `--planner-mode graph` follows source task/port identities. `--planner-mode legacy` retains the previous warehouse-specific rules for comparison.

The existing `procedures_path=` and `ddl_path=` API arguments are retained; new callers can use `procedures_paths=[...]` and `ddl_paths=[...]`.

All supporting files are consumed. A mixed procedure/DDL file can contribute to both collections. Conflicting definitions are rejected instead of silently choosing a source. Identical repeated definitions are accepted. Unqualified calls use the configurable `default_schema` hint (default `dbo`); this is not a claim to reproduce every SQL Server principal-specific resolution rule.

## Outputs and interpretation

Start with the Markdown review and `_Migration.ipynb` draft. The UI keeps diagnostic files behind an advanced option. JSON artifacts are records, not scripts to execute.

`migration_report.json` is the run index. Each package/input version contains:

- `migration_manifest.json`: content hashes, engine/configuration identity, and artifact paths.
- `package_ir.json`, `analysis.json`, `migration_plan.json`, `source_inventory.json`.
- `task_graph.json`, `dataflow_graphs.json`, and `dependency_inventory.json` preserve identities, scopes, graph edges, and definition provenance.
- `verification_plan.json` lists required comparisons. It contains no executed comparison results.
- `operations.json`: original generation output for diagnosis, including rejected content.
- `validation_results.json`, `assessment.json`, and a Markdown review report.
- `pending_items.json` indexes named sections with stable IDs, original SQL, connections, procedure availability, and bindings.
- A `_Migration.ipynb` graph review draft for every non-dry-run assessment. The filename suffix is retained for compatibility; it does not indicate certified migration.

Incomplete graph notebooks have `notebook_status = DRAFT_REQUIRES_REVIEW` and `execution_readiness.status = BLOCKED_BY_REVIEW`. Static checks passing still do not imply runtime equivalence.

Statuses include `REVIEW_REQUIRED`, `ARTIFACTS_GENERATED`, `ANALYZED`, `PARTIAL_FAILURE`, and `FAILED`. There is no unconditional `MIGRATED` result.

Operation coverage is the percentage of generated non-structural operations passing native-code static checks. Container entry/completion bookkeeping is validated and counted separately. It is **not** task coverage, runtime validation, or behavioral accuracy. All source tasks/components have a disposition; unsupported standalone work is retained as a review step rather than disappearing.

Content-addressed output versions change with source/dependencies, referenced metadata, configuration, and engine code. Same-input findings retain stable IDs. Generated notebook hashes are recorded in the manifest. A changed notebook is preserved even without a review marker; explicitly reviewed, unreadable, and untracked notebooks are also protected. Preserved outputs stay downloadable and require revalidation. They do not count as newly analyzed or generated packages. These policies concern generated artifacts, not data-pipeline rerun safety.

## Current boundary

The source-graph planner supports standalone relational SQL, explicit table overrides or visible default naming, sequence boundaries, success-AND precedence, static mapped OLE DB sources, Multicast, a restricted Derived Column expression subset, and ordered Conditional Split outputs. It uses exact port IDs and never flattens a multi-source graph into the first source.

Data-flow destination projections are previews. Their writes remain blocked by `LOAD_STRATEGY_REQUIRED` until load semantics are agreed. Explicit single-statement DML already present in an Execute SQL Task can be translated; that preserves supplied source behavior rather than choosing a new load strategy.

Source access modes select the active table, SQL command, or variable. Inactive captured SQL is not used as dependency evidence. Variable-backed pending sections retain lexical scope, declaration/value, expression-evaluation flags, and runtime variable/parameter references; expressions are not evaluated or interpolated into executable SQL.

Missing procedures produce named draft sections. Supplying a definition regenerates the source step in a new input version; a parameterless call to a single supported relational statement can become native Spark SQL. Other procedure bodies and argument/result bindings remain pending with their available definitions recorded.

Loops, Script Tasks, joins/lookups/aggregates, general procedural SQL, parameter/result bindings, row-error redirection, event handlers, checkpoints, forced results, cycles, and conditional/failure/completion/OR task constraints remain explicit blockers. Independent task branches require a concurrency/failure-isolation decision before executable notebook acceptance. The optional legacy planner still uses warehouse/name/control-table conventions and reports those assumptions.

No deployment, runtime equivalence measurement, Volume browsing, or new pipeline/load strategy is implied. See [the source-graph guide](docs/source-graph.md) for mapping examples, supported subsets, and validation steps.

Static validation rejects visible Spark UDF/Pandas UDF/UDTF definitions and registration, selected custom row APIs, dynamic Python execution, unresolved SQL parameters, legacy EXEC passthrough, incomplete bodies, and unbounded driver collections. Ordinary Python helpers and explicitly bounded one-row retrieval remain supported. This is a conversion-quality check, not a sandbox for arbitrary Python.

See [the assessment contract](docs/assessment-contract.md) for details and follow-on acceptance criteria.

## Tests

```sh
pytest -q --ignore=tests/agent/test_live_llm_integration.py
```

The live integration module requires a separately configured Databricks endpoint; do not use it for offline validation. Source code and examples in this repository are the only inputs used by automated tests. Confidential third-party tool archives are not included.
