# Phase 8, third increment: TurboIntegrator section and control-flow analysis

## Delivered boundary

This increment adds offline analysis of explicitly mapped TurboIntegrator source
sections. It follows the separate [MDX](phase8-mdx-foundation.md) and
[rules/feeder](phase8-tm1-rules-feeders.md) analyzers. It does not register the TM1
package adapter, generate Databricks code or claim source/target equivalence.

The accepted input is a version 1.0 `ti_section_files_v1` manifest naming plain
UTF-8/UTF-8 BOM Prolog, Metadata, Data and Epilog source files with their raw-byte
SHA-256 hashes. The process name and export/server version are supplied
declarations or `null`; neither is inferred from filenames, variable names or
numeric export records. Unknown formats and section roles are rejected.

Native serialized `.pro` layouts are explicitly unsupported. A versioned decoder
requires representative, supplied exports and regression fixtures. Numeric
records and textual headings are not used to guess section boundaries. The
manifest format is an explicit interchange format for this analyzer, not a
claim to decode an IBM export version.

An absent section produces `MISSING`, absent source and blocked structural
completion. An explicitly supplied empty file produces a complete empty AST and
nominal entry-to-exit graph. Missing references to declared files or stale hashes
are intake errors rather than inferred empty source.

## Preserved structure and review requirements

| Construct | Representation and limitation |
| --- | --- |
| Assignments and scalar expressions | Original spelling, operands, call arguments, source order and Unicode character spans. No expression evaluation, coercion or parameter/row-role inference. |
| IF / ELSEIF / ELSE / ENDIF | Ordered branches with preceding-false/current-true guard vectors. Conditions are not evaluated. |
| WHILE / END | Conditioned entry/exit and loop-back paths, plus a termination-review dependency. |
| ItemSkip, ItemReject, ProcessQuit, ProcessError, ProcessBreak | Distinct unresolved control transfers. No assumed successful process/section exit, next-record destination or transaction outcome. Later source statements remain visible. |
| CellGetN/S, CellPutN/S, CellIncrementN | Declared cube reads/writes/increments; correct target-argument position and complete source arguments retained. Dimension order, types and write behavior remain unresolved. |
| ExecuteProcess / RunProcess | Separate synchronous/asynchronous call sites, named parameter/value arguments and return-value use. Child definitions, return statuses and asynchronous completion require evidence. |
| DataSource assignments | Every conditional/repeated explicit configuration assignment retained, with literal string only when directly present in source. No datasource lifecycle is established by evaluating configuration. |
| ASCII/TextOutput and ODBC calls | File/connection references and embedded SQL preserved. No output path is followed and no SQL dialect is guessed. |
| ExecuteCommand, clears and destructive metadata calls | Declared effects and redesign/review dependencies. Nothing is executed. |
| DimensionElementInsert / ViewCreate / SubsetCreate | Metadata mutation call sites; element type, coordinate/dimension binding and runtime effects still require review. |

The graph scope is `NOMINAL_INTRA_SECTION_ONLY`. Metadata and Data are separate
`SOURCE_DEPENDENT_SEPARATE_RECORD_PASS` slots; they are never combined into a
single per-record loop. Prolog/Epilog have nominal process-stage scope. The
report deliberately has no automatic inter-section execution edges or verified
lifecycle even if the source assigns `DataSourceType='NONE'`. Runtime source
configuration, skip/error behavior and process orchestration remain unresolved.

This bounded grammar supports single-quoted strings with doubled quotes,
numeric spellings, variables, operators and selected function signatures. Hash
comments are ignored, while include/import-like directives are reported as
unsupported. Rule area/context syntax is rejected. Unknown functions, unsupported
signatures and other unsupported constructs remain explicit findings; malformed
or limited sections do not retain a successful parsed prefix. Other complete
sections remain available in the same report.

`STRUCTURED` establishes complete parsing within this grammar. It does not prove
that a source TM1 server accepts the process, verify operator/coercion behavior or
establish that any declared call executes. Variable names such as `pWatermark`
never establish incremental loading. `incremental_logic` stays `NOT_DETERMINED`.

## Evidence, validation and acceptance

The versioned report retains all decoded source text and its hash/size, original
file inventory hashes, source-relative spans, ordered statement IDs, ASTs,
nominal graphs, symbol references, declared effects and missing dependencies.
Literal targets are declared references, not proof those objects exist. Dynamic
and empty targets remain unresolved; no object, schema or procedure is invented.

The offline schema and public validator check content identity, source hashes,
section roles and missing/empty consistency, AST ordering/containment, source
evidence, unique reference/dependency identities, and derived statement/graph
consistency. Recomputing a report hash cannot legitimize a changed guard vector,
omitted statement index or altered lifecycle scope. Hashes are integrity checks,
not authentication or source-engine validation.

Every report has `semantic_status: NOT_VERIFIED`,
`lifecycle_status: SOURCE_DEPENDENT_NOT_VERIFIED`, no inferred assumptions and
`migration_accepted: false`. All runtime bindings/dependencies are unresolved.
There is no weighted semantic credit or acceptance merely from analysis success.
Critical-rule review, controlled execution and data reconciliation remain gates
for a later migration implementation.

## Interfaces, limits and compatibility

```bash
.venv/bin/python -m ssis_migration.adapters.tm1.ti \
  --manifest examples/tm1/ti/manifest.json \
  --output /tmp/new-ti-analysis.json
```

The CLI resolves only declared section paths, rejects non-regular/symlink input
files, checks all input bytes for freshness and exclusively creates new output.
Exit codes are 0 for bounded structural completion, 1 for unsupported, invalid
or limited analysis, and 2 for file, evidence or argument errors. Exit 0 is not
migration acceptance. Source SQL, commands, process calls and source filenames
inside expressions are never executed or followed.

Programmatic callers supply each section as exactly
`{'text': decoded_text, 'file_id': inventory_file_id, 'locator': source_locator}`:

```python
report = analyze_ti_sections(
    sections, export_format='ti_section_files_v1',
    process_name=None, source_export_version=None,
)
validate_ti_analysis(report)
```

Each section is bounded to 1 MiB of UTF-8 source, 50,000 tokens, block nesting
16, expression recursion 48 and AST depth 24. Source-language limits never
establish success on a parsed prefix. No dependency was added.

Shared TM1 tokenization and the existing scalar-expression mechanics are reused.
The TI grammar overrides name/call/statement interpretation and rejects rule-only
syntax. No rule, MDX or SQL analysis fallback occurs. Existing SSIS parsing,
IRBuilder-before-analysis order, provider abstraction, migration_intent,
Databricks generation and acceptance interfaces are unchanged.

| Changed file | Purpose |
| --- | --- |
| `adapters/tm1/syntax.py`, `rules/lexer.py`, `rules/parser.py` | Reuse bounded lexical and scalar parsing mechanics without duplicate scanners. |
| `adapters/tm1/ti/parser.py`, `control_flow.py` | TI statement grammar, ordered source index and nominal guarded flow. |
| `adapters/tm1/ti/analysis.py`, `__init__.py` | Evidence-bound report, dependencies, effects and integrity validation. |
| `adapters/tm1/ti/__main__.py` | Explicit manifest/file intake and offline CLI. |
| `schemas/tm1_ti_manifest.v1.json`, `tm1_ti_analysis.v1.json`, `schemas/__init__.py` | Packaged versioned schemas and offline registration. |
| `intake/inventory.py` | Unconfirmed `.ti`/`.pro` hints before SQL fallback; actual signatures retain blocked TM1 adapter detection. |
| `tests/adapters/tm1/test_ti*.py`, `fixtures/ti_sales_summary.json` | Grammar/control-flow, effects, unsupported input, integrity, file-intake and portable golden checks. |
| `tests/intake/test_tm1_hints.py`, `examples/tm1/ti/` | Hint regressions and a clearly synthetic, hashed example. |

Production paths are relative to `src/ssis_migration/`; test fixture paths are
relative to `tests/adapters/tm1/`.

## Verification and next step

Full regression suite: **825 passed, 12 skipped**. Eight tests require the .NET
SDK, unavailable in this environment, and four are existing live integrations.
The final focused TI/rules/MDX/intake/schema suite passed **217 tests**, including
additional missing-source consistency and limit/partial-section checks. The
existing C# compilation and equivalence implementations were not changed.

The synthetic golden example preserves nine statement nodes, five declared
effects and 24 missing dependencies. Offline wheel build and wheel-only CLI /
packaged-schema validation passed outside the checkout, including a check that
the package adapter remains blocked. No TI process or Databricks workload ran.
Changes are local on `GenericAITool-version01` and are not committed or pushed.

The subsequent [metadata increment](phase8-tm1-metadata-binding.md) adds supplied
dimensional metadata and parameter/datasource declaration binding. Lifecycle
evidence remains unverified. Next is TM1 adapter / neutral-IR /
migration-contract integration.
Native `.pro` decoding also needs representative versioned export fixtures.
The legacy-IR projection boundary will not be bypassed with an invented SSIS IR.
