# Audit configuration across workloads

The graph planner reads source structure, SQL, bindings, variables, connections, and component types. Package names and task captions do not select business implementations. Root executable IDs, event-handler IDs, and package names may differ. Source mode retains supported source audit behavior. Target mode deliberately replaces selected audit work with an adapter to existing audit storage. This adaptation still requires review of its meaning in the target pipeline.

General intake does not mean universal executable conversion. Every parsed task/component receives a disposition. An unknown operation remains a named review section and blocks execution; it never becomes an empty passing implementation. Missing business procedures remain pending until their definitions arrive and supported translation succeeds. Legacy behavioral match stays `NOT_MEASURED` until actual comparison evidence exists.

## Source audit rules

Use `--source-audit-rules rules.json`, Python `source_audit_rules={...}`, or the frontend's **Source audit classification rules (JSON)** field. Omitted settings keep the built-in profile. All rules participate in the output-version fingerprint and are recorded in `audit_replacement_plan.json`.

```json
{
  "use_builtin_signatures": false,
  "insert_signatures": [
    {"role": "execution", "columns": ["FlowCode", "StartedAt", "RunState"]}
  ],
  "objects": [
    {"role": "execution", "name": "telemetry.RunLedger", "connection": "AuditConnection"}
  ],
  "tasks": [
    {
      "package": "ExamplePipeline",
      "source_id": "Root\\RecordRun",
      "role": "execution",
      "outputs": ["User::RunKey"]
    }
  ]
}
```

- **INSERT signatures:** column names are compared without case sensitivity. A signature must be a subset of an INSERT's explicit columns. Matching identifies that connection/object as audit storage; subsequent supported INSERT/UPDATE/query-only tasks using exclusively those audit objects can be replaced. Disable the built-in signatures when their columns also occur in business tables. Signatures are declarations of your audit conventions, not a proof that a table has no business purpose.
- **Objects:** declare dedicated audit objects when the package only updates or queries them. `connection` can be a captured reference or known alias. Omitting it deliberately applies the rule across connections. A same-named table on another connection remains business work when a connection is specified. The source inventory/dependency inventory supplies captured identifiers. Arbitrary procedural SQL, dynamic SQL, or a batch also accessing business objects is not automatically replaced by this rule.
- **Tasks:** explicitly declare an inspected audit leaf operation when its implementation is unavailable or has a different format. `package` matches the DTSX ObjectName, not its upload filename; omitting it applies the source ID rule to every selected package. Use the exact `source_id` from `source_inventory.json`. Treat this as an explicit declaration that the whole leaf performs only auditing. SQL result/output parameter bindings and script ReadWriteVariables are inspected for produced values. Opaque tasks require an explicit `outputs` list, including `[]` if there are no outputs. Do not mark a leaf containing business work as audit. Containers, loops, and data flows cannot be removed by these task rules.

A package-scoped rule for another package is ignored during that package's assessment, which allows one configuration for a batch. An applicable missing task ID, overlapping wildcard/package task rules, invalid configuration, or unsafe nonleaf rule is reported or rejected. Unsupported source formats do not become recognizable merely because an object name is listed. Renamed source objects/tasks require updated explicit rules where the old identities were used.

Replacement evidence retains the original SQL/expression, parameter/result bindings, output variable references, reason, configured rules, and source IDs. The original uploaded package remains the complete source for opaque programs. Source audits mixed into business SQL, data-flow writes to recognized source audit objects, and unknown error-recovery work remain review blockers.

If replaced audit output still feeds business work, select an explicit `audit_value_bindings` entry. `runtime_input` requires the chosen business value in `ssis_runtime_values`; `notebook_key` requires a captured Int64 declaration. Source defaults are removed for these bridges. No Int64-to-Int32 narrowing or fallback to an old audit key is performed.

## Target storage contract

Use `--audit-contract contract.json`, Python `audit_contract={...}`, or **Target audit storage contract (JSON)**. An empty object uses the documented `audit_notebooks`/`audit_tables` layout. Overrides rename physical tables/columns while retaining canonical role meaning and types:

```json
{
  "notebook_table": "pipeline_runs",
  "table_stats_table": "pipeline_table_metrics",
  "notebook_columns": {
    "notebook_key": "run_id",
    "master_key": "parent_run_id",
    "notebook": "pipeline_name",
    "status": "run_state",
    "duration_time": null,
    "job_id": null,
    "job_name": null
  },
  "table_stats_columns": {
    "notebook_key": "run_id",
    "master_key": "parent_run_id"
  },
  "statuses": {
    "Running": "IN_PROGRESS",
    "Success": "COMPLETED",
    "Error": "FAILED"
  }
}
```

Unspecified roles keep their canonical physical names. Run/table-metric tables must be distinct. Column mappings within each table must be one-to-one. Lifecycle values must be distinct. Physical names are quoted, including names with spaces or backticks; status/message values remain bound SQL parameters. Catalog/schema are supplied separately through `audit_catalog`/`audit_schema`.

| Table | Canonical roles | Required type |
| --- | --- | --- |
| Run | notebook_key, master_key | bigint |
| Run | notebook, status, message | string |
| Run | time_start, time_end | timestamp |
| Run | duration_time | decimal(10,1); may be omitted with null mapping |
| Run | user_id, source_file_path, target_table | string; may be omitted with null mapping |
| Table metrics | table_key, master_key, notebook_key | bigint |
| Table metrics | time_start, time_end | timestamp |
| Table metrics | user_id, caller_notebook, schema_name, table_name | string |
| Table metrics | before_row_count, before_table_version, before_storage, after_row_count, after_table_version, after_storage | bigint |
| Both | job_id / job_name | bigint / string; used only when present, or explicitly omitted with null mapping |

Required role mappings cannot be null. Use `audit_capture_table_stats=False` / `--no-audit-table-stats` when table metrics are not part of the selected framework. The metrics table is checked only when metrics will be written. The run table is always checked. All storage must already exist and use Delta; incompatible types or extra unknown non-nullable columns stop execution before business mutations. No schema repair, identity/sequence emulation, triggers, or arbitrary framework functions are inferred.

Custom status values apply to inserted records, attachment checks, and final updates; the internal lifecycle remains Running/Success/Error. Attached execution keys must identify exactly one Running record. New execution keys are positive Int64 UUID-derived values. An existing framework that requires different key allocation, additional required fields, different statistics semantics, or different parent/child behavior needs a separately implemented adapter rather than a column rename.

The supplied reference libraries are inspected as data. Uploading a reference does not infer these mappings, execute a library, register a UDF, or certify its schema. Ordinary Python helpers implement notebook-local state; no Spark/Pandas UDF or unbounded data collection is generated by this adapter.

## Batch input and failures

The frontend accepts multiple DTSX uploads. Staging hashes the selected filename/content set, preserves distinct same-named contents, deduplicates identical filename/content pairs, and excludes previous selections. CLI/API directory intake recurses through `.dtsx` files without extension case sensitivity. Each package has independent assessment, findings, notebook draft, provenance, and input/configuration version. A malformed or unreadable package records its own failure; valid siblings continue. Batches do not implement cross-package orchestration, parallelism, or shared audit state.

## Current conversion boundaries

| Source feature | Result |
| --- | --- |
| Supported relational Execute SQL and bound single-row results | Native Spark SQL with captured bindings; target identities and types still need verification |
| Available stored procedures in the bounded relational subset | Native inlined bodies; unsupported procedural statements remain pending |
| Supported OLE DB flows, column transforms, multicast, routing, append to existing destination | Native DataFrame/Spark expressions with graph/type/write checks; source TRUNCATE remains a separate ordered task |
| Bounded scalar Expression Tasks and supported source mail programs | Ordinary native helpers and source-derived logic in source mode; unsupported expressions/scripts remain pending |
| Configured or recognized audit-only leaf work | Explicit target replacement evidence and native target lifecycle; excluded from business-operation coverage |
| Flat ordered package OnError | Supported source-mode dispatch, or recognized audit-only chain replacement in target mode; unknown recovery stays blocked |
| Missing business procedure/script bodies, unknown tasks/providers/components | Named pending implementation; no fabricated business logic |
| Loops, Execute Package/File System/custom tasks, unsupported joins/expressions | Represented review work requiring implementation |
| Unsupported transactions, conditional main paths, parallel/nested handlers, checkpoint semantics | Execution blockers until their semantics are implemented/reviewed |
| Target framework notebooks/ZIP libraries/DDL references | Inspection-only contracts/findings; never executed or substituted for legacy definitions |
| Deployment and legacy behavioral comparison | Not implemented/executed by generation; no accuracy percentage is inferred |

These are bounded capabilities rather than a guarantee that every operation in a feature family translates. See [source-graph.md](source-graph.md) for the supported forms. Review drafts are always available in graph mode; unresolved findings stop notebook execution before target audit setup or business work. Runtime validation must cover actual Delta UPDATE/history behavior, deployed schemas, target caller integration, permissions, concurrency, failures, reruns, and source/target data comparison.
