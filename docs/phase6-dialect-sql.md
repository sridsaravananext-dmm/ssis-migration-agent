# Phase 6: dialect-aware SQL analysis and structural preservation

This phase introduces a shared SQL-language boundary using the existing
SQLGlot dependency. It strengthens the existing feature detector, SQL resolver
and translation validator, and adds an offline file-analysis command. It does
not introduce another target platform or switch the SSIS migration pipeline
to a new planner.

## Behavior changes

The old validator compared the first statement's projection counts and table
sets. It could pass changed filters, projections or join conditions. Cutoff
checks also contained customer-specific column/parameter names, and feature
detection inferred cutoffs from parameter names alone.

The new analyzer requires an explicit T-SQL, Oracle SQL, Spark SQL or Databricks
SQL dialect. It analyzes every nonempty statement in order. Comment-only
fragments do not count as executable statements. Malformed later statements
prevent a valid first statement from concealing the failure. Unsupported
procedural constructs, opaque parser fallbacks, unresolved functions and SQL
Server session state remain explicit diagnostics.

Version-1 reports include the input SQL digest, statement/AST locations,
features, named parameter references, declared table read/write references,
CTE references and parameterized range candidates. Table references remain
`DECLARED_ONLY`; schemas, procedure definitions and data are not invented or
retrieved. Oracle CTE resolution follows its defined unquoted identifier
folding; other engines' missing collation/case configuration is not invented.

A range candidate requires positive conjunctive lower and upper parameter
bounds on the same column in the same WHERE scope. Bounds in unrelated
queries, different columns, OR/NOT branches, SELECT expressions or strings do
not establish a range. Reversed comparisons and parameterized BETWEEN forms
are recognized. The compatibility feature name `INCREMENTAL_CUTOFF` remains,
but means a **candidate filter**, not verified CDC, SCD, incremental loading or
a persisted watermark update.

The validator now compares statement count, projection expressions/aliases,
filters, joins, grouping/windows, table occurrences, UNION flags and the full
ordered AST. It compares every candidate range in every statement/query scope.
Absent required features cannot pass vacuously, and unknown requirements
cannot be ignored. Named parameter syntax is normalized for comparison;
actual runtime parameter bindings remain unverified.

The expected structure is the supported source AST rendered into the target
dialect with SQLGlot's strict unsupported-feature handling, then parsed again.
Locations and comments do not affect comparison. This is a conservative static
check: equivalent SQL written with a different AST may require review.
Matching rendered ASTs do not establish equivalent types, null behavior,
collation, time zones, bindings or data results.

SQL Server temporal reads and Oracle historical/flashback reads cannot be
rewritten automatically into Delta time travel. They require an explicit
historical-data strategy and supporting source evidence.

The existing low-level resolver retains its single-statement translation
boundary, now checking complete source/target structure and refusing opaque
passthroughs or batches. The validator can compare complete batches, but the
resolver does not silently pick one statement or generate a batch job.
`RESOLVED` remains a static translation result, never migration acceptance.

## Interfaces and use

The existing validator/resolver signatures and original dataclass constructor
fields remain available. Additional defaulted analysis fields and serialized
scope/evidence fields are additive. Previously permissive acceptance of empty,
opaque or unsupported SQL is intentionally tightened. Existing supported SSIS
regression cases remain unchanged.

Python API:

```python
from ssis_migration.languages.sql import analyze_sql

report = analyze_sql(
    'SELECT id FROM exported_data WHERE changed_at > :low AND changed_at <= :high',
    dialect='oracle',
)
assert report['semantic_status'] == 'NOT_VERIFIED'
```

Analyze a supplied file into a **new** report file (the output directory must
exist):

```bash
python -m ssis_migration.languages.sql \
  --source /path/to/export.sql \
  --dialect tsql \
  --output /path/to/sql-analysis.json
```

The CLI preserves the raw file's path, SHA-256 and byte count, checks source
freshness, and refuses to overwrite source exports or prior reports. UTF-8 BOM
removal affects only the decoded SQL digest; the raw-file digest still binds
the exact supplied bytes. Exit codes are 0 for structurally analyzed SQL, 1
for a written report with unsupported/unresolved input, and 2 for CLI/file
errors. No SQL, Python expression, AI request or target workload is executed.

`validate_document(report, 'sql_analysis')` validates the packaged version-1
schema without retrieving remote schemas.

## Current limits and next phases

Oracle **relational SQL** is analyzed; PL/SQL packages, procedures, functions
and procedural control flow remain unsupported pending their own phase.
Unknown/UDF definitions require supplied evidence and further analysis.
This phase does not add MDX, TM1 rules, TurboIntegrator or C# parsers. It does
not replace the existing SSIS procedure extraction/regex translation paths or
claim their entire behavior is verified. The new boundary is available through
the SQL APIs, existing resolver APIs and offline CLI; automatic neutral-IR
contract/planner consumption follows separately.

The remaining sequence is:

7. Neutral-IR planning/contracts integration and SSIS C# assessment.
8. TM1 adapter with separate MDX, rules and TurboIntegrator analysis.
9. Informatica PowerCenter adapter.
10. SAP BODS adapter.
11. ODI adapter and Oracle procedural semantics.
12. Broader Databricks generation, controlled execution and data reconciliation.

Each adapter phase remains incremental; unsupported exports/constructs stay
explicit. The only framework target remains Databricks. Acceptance still needs
at least 80% weighted verified behavior plus critical and mandatory gates.

## Changed files

| File | Purpose |
| --- | --- |
| `languages/__init__.py`, `languages/sql/` | Explicit SQL dialect boundary, complete-input analysis and offline CLI. |
| `schemas/sql_analysis.v1.json`, `schemas/__init__.py` | Packaged versioned report validation. |
| `resolver/sql_features.py` | Reuse AST analysis and remove naming-based cutoff inference. |
| `resolver/sqlglot_resolver.py` | Refuse empty/opaque/batch/history passthroughs and retain analysis evidence. |
| `resolver/resolution_engine.py` | Keep source/target analysis when static resolution is blocked. |
| `validator/sql_semantic.py` | Complete structural preservation checks with explicit unverified semantics. |
| `tests/languages/test_sql_analysis.py`, `tests/validator/test_sql_preservation.py` | Analysis, preservation, integrity, CLI, unsupported input and no-execution cases. |

Source paths in this table are relative to `src/ssis_migration/`, except tests.

## Verification

- Full suite: **507 passed, 4 live integration tests skipped**.
- **97 focused SQL/resolver checks passed** with both installed SQLGlot
  30.24.0 and the declared minimum 26.0.0, isolated without changing the
  working environment's dependencies. This phase adds 84 cases.
- A wheel was imported outside the checkout. Its CLI analyzed both SQL batch
  statements into a schema-valid, file-bound report. Changed filters failed
  preservation checks. An Oracle relational translation passed static checks
  while retaining `NOT_VERIFIED` semantic equivalence.
- The packaged SSIS sample still generated artifacts and its 192-node neutral
  graph, with acceptance `BLOCKED` and execution `NOT_RUN`.
- No live AI request, target workload or deployment ran. Diff checks passed.
