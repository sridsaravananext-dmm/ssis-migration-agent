# Project Context — SSIS Migration Agent

## 1. Purpose

Build a reusable, AI-assisted migration framework that analyzes an ETL/source
system, captures its behavior in structured representations, and proposes a
target implementation for human review.

The current implementation focus is SSIS-to-Databricks. The longer-term
architecture should remain extensible to other source and target platforms
without hard-coding the entire framework around one pair of technologies.

## 2. Architectural principles

- Preserve source behavior before attempting optimization or redesign.
- Use parsed source artifacts, exported metadata snapshots, and deterministic
  analysis wherever possible.
- Use AI to assist with complex or ambiguous migration logic, not to invent
  missing behavior.
- Record assumptions, unresolved semantics, risks, and validation requirements.
- Require human review before connecting to real target systems, executing
  changes, migrating data, or deploying generated code.
- Do not claim that a migration is complete merely because code or DDL was
  generated.
- Keep source/target-specific behavior behind replaceable adapters where
  practical.

## 3. Main analysis pipeline

The established SSIS analysis pipeline is:

1. Parse the `.dtsx` package.
2. Build the intermediate representation (IR).
3. Analyze the IR.
4. Build migration artifacts and reports.

The correct order is:

```python
parsed = SSISPackageParser(package_path).parse()
package_ir = IRBuilder().build(parsed)
analysis = analyze_package(package_ir.to_dict())
```

Do not pass the raw parser output directly to `analyze_package`.

## 4. Metadata snapshots and connection mappings

The example mapping file is:

`examples/wwi/connection_mapping.json`

It maps SSIS connection-manager references to exported database metadata
files. The current example maps:

- `Project.ConnectionManagers[WWI_Source_DB]`
  to `examples/wwi/database_metadata_wwi.json`
  with role `source`.
- `Project.ConnectionManagers[WWI_DW_Destination_DB]`
  to `examples/wwi/database_metadata_wwi_dw.json`
  with role `destination`.

A mapping is configuration; it is not itself a live database connection.
The context builder loads the referenced metadata files, builds database IR,
and selects relevant objects and dependency edges.

This allows analysis and planning to proceed from snapshots without requiring
a live database connection during every run. A later validation or execution
stage may require explicit connectivity and approval.

Never put passwords, access tokens, or other secrets in the mapping file.

## 5. Migration contract and deterministic resolution

`MigrationContract` is intended to capture structured migration evidence,
including source behavior, required behavior, target capabilities/design,
assumptions, unresolved items, semantic risks, validation requirements, and
status.

`DeterministicMigrationResolver` consumes a contract dictionary and resolves
a generation strategy and status from known features and unresolved items.
Its documented scope excludes executing SQL/Spark code, modifying data,
guessing unverified behavior, and using an LLM to make its decision.

`determine_migration_strategy()` in
`src/ssis_migration/planner/migration_strategy.py` is a separate strategy
function based on procedure-pattern signals. Do not assume the two mechanisms
are unified; inspect their callers and tests before changing their relationship.

## 6. AI-assisted generation

The AI orchestration code coordinates context construction, planning, prompt
building, provider invocation, response validation, and static validation of
generated code.

The repository contains multiple provider implementations. Provider selection
and exact integration behavior should be confirmed from the current provider
factory and tests before being described as production-ready.

Generated code is a proposal until it has passed appropriate review and
target-specific validation.

## 7. Distinct migration execution paths

### Analysis pipeline

`src/ssis_migration/migrate.py` supports package analysis and reporting.
The observed report uses `ANALYZE_ONLY` mode and reports
`ANALYSIS_COMPLETE_DEPLOYMENT_PENDING`.

The observed report explicitly indicates that deployment is pending.
Target metadata discovery, DDL generation, transformation generation, and
job/pipeline generation are not represented as a completed end-to-end flow
by this report.

### Structure migration runner

`src/ssis_migration/migration_runner.py` exposes
`run_structure_migration(...)`. The observed example report is a `DRY_RUN`.
It plans schema/table statements but reports that they were not executed.

The runner has an execution option. Treat execution as a separate, explicitly
approved operation; inspect the current implementation and safeguards before
using it against a real catalog.

Do not conflate the analysis-only pipeline with the structure migration
runner.

## 8. Current sample

The primary sample package is:

`examples/wwi/DailyETLMain.dtsx`

The example uses Microsoft WideWorldImporters and WideWorldImportersDW
metadata snapshots. The local SSIS workflow previously inspected follows a
pattern involving table-name setup, lineage-key creation, staging cleanup,
cutoff retrieval, source update procedures, and destination migration
procedures. Preserve these behaviors when modeling the migration; do not
assume that simply inserting into the final table is equivalent.

## 9. Human approval boundary

The intended workflow is:

1. Analyze source artifacts and available metadata.
2. Generate a migration proposal and evidence.
3. Review assumptions, unresolved items, semantic risks, DDL, and code.
4. Configure connections explicitly when needed.
5. Validate against the intended target environment.
6. Execute/deploy only after the appropriate approval.

The system must not silently connect to production systems or create/modify
target objects as a side effect of analysis.

## 10. Documentation discipline

Treat actual repository files, tests, and run reports as the source of truth.
When a behavior has not been inspected or tested, label it as unverified.
Update `CURRENT_STATUS.md` after meaningful implementation or test changes.
