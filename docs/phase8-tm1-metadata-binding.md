# Phase 8, fourth increment: dimensional metadata and process binding

## Architecture audit and scope

The existing `database/importer.py`, `database/models.py` and
`database/connection_mapping_loader.py` model relational tables, procedures and
SSIS connection mappings. `context/builder.py` requires a legacy `PackageIR` and
extracts SSIS SQL dependencies. Reusing these models for TM1 cubes would invent a
relational interpretation and bypass the existing neutral-IR projection boundary.
They remain unchanged.

This increment instead adds a source-specific metadata catalog and declaration
binding overlay over existing MDX, rule/feeder and TurboIntegrator reports. It
reuses packaged offline schemas, intake file identities, content hashing and all
three public analysis validators. It does not change parsing or add connections,
AI calls, code generation, deployment or a TM1 package adapter.

The pre-change focused suite passed **217 tests**. Acceptance tests for this
increment cover exact identities and provenance; ordered cube coordinates;
hierarchy/member ambiguity; missing/cyclic consolidation links; child-process
parameter types; explicit datasource-column roles; input freshness and tampered
report rejection. Implementation is confined to the new metadata boundary and
schema registration, plus examples, tests and documentation.

## Supplied metadata format

`tm1_metadata.v1.json` defines the explicitly selected `tm1_metadata_v1` JSON
interchange format. This is a documented declaration format for file-based input,
not a decoder for a native IBM export. The required `source_system` declaration
scopes a catalog; metadata files declaring different source systems or conflicting
versions cannot be merged. Export/server version can be `null`, which stays an
unresolved binding. No server version is inferred from a filename.

| Collection | Required declarations and boundary |
| --- | --- |
| Dimensions/hierarchies | Canonical names, member list and explicit default member or `null`. Multiple hierarchies remain distinct; no default hierarchy is guessed. |
| Members | Canonical name, declared N/S/C type and weighted child links. Weights retain decimal/scientific string spellings, including negatives. No aggregation or consolidation value is calculated. |
| Cubes | Ordered dimension/coordinate-hierarchy pairs. This order binds positional DB/CellGet/CellPut arguments; it does not establish cell-value types or runtime write semantics. |
| Subsets | Dimension, hierarchy, public/private scope, private owner, and either static member names or an unevaluated expression. Missing definitions remain missing. |
| Processes | Explicit parameters, variables and datasource name or `null`. A process declaration is not its executable definition. |
| Parameters | Canonical name, STRING/NUMERIC type, declared required flag and optional type-checked default. Defaults are preserved, never substituted into source expressions. |
| Variables | Name, declared type and explicit LOCAL, SYSTEM or DATASOURCE_COLUMN role. Row variables require an explicit datasource column and matching declared type. Prefixes such as `p`, `v` or `n` do not assign roles. |
| Datasources | ASCII, ODBC, CUBE_VIEW, NONE or OTHER kind; explicit columns and file/connection/cube/view references; optional declared SQL dialect. Source file and SQL references are never followed or executed. |

All top-level collections are explicit arrays. Unknown fields, unsupported format
versions, conflicting parameter-default types, duplicate JSON keys, non-finite
JSON values and invalid shapes are rejected. Partial metadata can use empty
collections or permitted `null` fields; missing references are then reported.

Canonical identifiers match exactly. There is no case/whitespace normalization,
fuzzy matching, alias substitution or implicit MDX key conversion. Source-server
identifier equivalence remains a later review requirement.

Multiple metadata files can contribute different definitions to the same catalog.
Duplicate object identities remain separate candidates even when their content
is identical. Duplicates, missing hierarchy/member references, invalid default
members, duplicate cube axes, non-consolidated members with children, cyclic
consolidations, invalid row-column bindings and contradictory NONE datasources
produce evidence-backed catalog errors. Errors block safe declaration binding;
the tool never chooses the first candidate.

## Binding behavior

The source analysis is preserved unchanged in a separate, versioned
`tm1_metadata_binding` report. Every binding contains its source file/span when
available and exact metadata file/JSON-pointer candidates. Explicit rule-cube or
unnamed-process context is a declaration in the report, not invented source
evidence. MDX FROM cubes and TI process names retain their source declarations;
conflicting process context cannot override them.

| Source behavior | Binding result |
| --- | --- |
| Rule source cube | Requires explicit `source_cube` context; never inferred from a DB lookup, filename or sole catalog cube. |
| Qualified/unqualified rule selectors | Match only members of the declared source cube. Multiple possible dimensions/hierarchies stay ambiguous; repeated selectors for one dimension conflict. |
| DB/CellGet/CellPut coordinates | Check full tuple arity before assigning any coordinates. Literal member names bind to the declared ordered axis; dynamic expressions remain dynamic. |
| Rule dimension contexts and dimension calls | Bind declarations only. A `!Dimension` context does not become a runtime member value. Attribute semantics remain unresolved. |
| MDX paths | Distinguish dimension, hierarchy and member candidates; hierarchy/member collisions and repeated member names across hierarchies stay ambiguous. Alternate explicit hierarchies retain their identity. |
| MDX properties | An explicit hierarchy's declared default member can bind to metadata. CurrentMember and other runtime contexts remain dynamic. Children/Members/Parent traversal is not evaluated. |
| TM1SubsetToSet | Requires explicit hierarchy path, subset name and public scope for static declaration matching. Omitted scope, private-owner context and dynamic subset expressions remain unresolved. |
| TI symbols | Bind exact caller-process parameter/variable declarations and explicit row-column mappings. Binding a declared type does not prove definite assignment or an available runtime value. |
| ExecuteProcess/RunProcess | Match child declarations and named parameter/value pairs; check required omissions, duplicate names and known literal/caller-declared types. Dynamic expressions remain unverified. Return codes, process bodies and asynchronous completion still require evidence. |
| TI datasource configuration | Retain every conditional/repeated assignment. Compare literal declarations only; never evaluate branch conditions or substitute parameter defaults. |
| ODBC mapping | Bind a declared connection reference and dialect; do not parse, translate or execute embedded SQL. Multiple mappings remain ambiguous. |

ASCII server location references, ODBC DSNs and cube/view sources use their
respective declaration fields. A server location never supplies an unexported
client location; `client_location_reference` is an optional separate declaration.
Missing mapping fields/columns remain explicit. OTHER sources need further
capabilities. A supplied cube/view reference still requires the actual view
definition and lifecycle evidence.

Metadata and Data remain separate source-dependent record passes. Even a fully
declared NONE datasource does not establish that sections execute or how early
controls affect them. Configuration matching is not lifecycle verification.

## Integrity and acceptance

The report embeds original analysis and metadata snapshots. Metadata snapshots
retain original UTF-8 text including BOM, raw-byte SHA-256/size and parsed JSON;
validation checks that declarations match those bytes. Source analysis uses the
existing source/content validators. Inputs are copied, never mutated.

`validate_tm1_binding_report` validates packaged schemas and reconstructs all
bindings, candidate pointers, catalog issues, statuses and runtime requirements
from the saved inputs. A recomputed hash cannot legitimize a changed match,
invented evidence, omitted binding or removed requirement. This is deterministic
integrity validation, not authentication of metadata or source-engine validity.

Binding statuses are MATCHED_DECLARATION, MISSING, AMBIGUOUS, CONFLICT, DYNAMIC or
UNSUPPORTED. Overall status is:

- `BOUND_REVIEW_REQUIRED`: all requested bindings have supplied declarations.
- `PARTIAL`: declarations/context are missing, ambiguous, dynamic or conflicting.
- `BLOCKED`: catalog inconsistencies or incomplete/unsupported source analysis
  prevent safe progress.

All three retain `semantic_status: NOT_VERIFIED` and `migration_accepted: false`.
The source analyzer's dependency records remain verbatim in
`remaining_dependencies`; their original NOT_SUPPLIED flags are not rewritten as
runtime verification. The overlay shows which declarations are now available.
These broader requirements still need source review. No weighted semantic credit,
successful execution or critical acceptance gate is awarded by metadata matching.

Required later evidence includes source/parameter values, source record schema
verification, hierarchy/alias/key behavior, cell types, rules/feeders, aggregation,
security/default context, transaction/error semantics, actual child process/view
definitions and controlled source/target reconciliation.

## Offline interfaces

```python
from ssis_migration.adapters.tm1.metadata import (
    load_tm1_metadata, bind_tm1_metadata, validate_tm1_binding_report,
)

snapshot = load_tm1_metadata('supplied_metadata.json')
report = bind_tm1_metadata(analysis_report, [snapshot])
validate_tm1_binding_report(report)
# Rules: pass source_cube='ExplicitCube'.
# Unnamed TI processes: pass process_name='ExplicitProcess'.
```

`metadata_snapshot(raw_bytes, file_id=..., locator='json:/')` supports callers
that already inventory supplied files. Metadata is limited to 32 files and 8 MiB
per file. Consolidation checks use iterative traversal and indexed lookups; large
hierarchies do not consume the Python recursion stack.

```bash
.venv/bin/python -m ssis_migration.adapters.tm1.ti \
  --manifest examples/tm1/ti/manifest.json \
  --output /tmp/new-ti-source-analysis.json
.venv/bin/python -m ssis_migration.adapters.tm1.metadata \
  --analysis /tmp/new-ti-source-analysis.json \
  --metadata examples/tm1/metadata/synthetic_catalog.json \
  --output /tmp/new-ti-metadata-binding.json
```

Repeat `--metadata` for additional catalog files. `--source-cube` applies only to
rules and `--process-name` only to TI. The CLI rejects symlink/non-regular inputs,
checks input freshness and creates output exclusively, preserving existing
source/reports. It reads the supplied analysis report, not a live source system.

CLI exit codes: 0 for BOUND_REVIEW_REQUIRED, 1 for PARTIAL/BLOCKED reports and 2
for argument, schema, file or integrity errors. Exit 0 never means acceptance.
The synthetic example intentionally exits 1: source version, parameter-driven
file location and row-driven Product coordinate remain unresolved.

## Changed files and compatibility

| File | Purpose |
| --- | --- |
| `adapters/tm1/metadata/catalog.py` | Strict supplied metadata loading, original file snapshots, indexed catalog and reference/consolidation checks. |
| `adapters/tm1/metadata/binding.py` | Declaration overlay for MDX/rules/TI, provenance and deterministic report reconstruction. |
| `adapters/tm1/metadata/__init__.py`, `__main__.py` | Public offline API and CLI. |
| `schemas/tm1_metadata.v1.json`, `tm1_metadata_binding.v1.json`, `schemas/__init__.py` | Packaged versioned metadata/report schemas and offline registration. |
| `tests/adapters/tm1/test_metadata_binding.py`, `test_metadata_cli.py`, `fixtures/ti_metadata_summary.json` | Binding, ambiguity, unresolved semantics, evidence/tampering, file-intake and golden checks. |
| `examples/tm1/metadata/` | Clearly synthetic catalog and usage instructions. |

Production paths are relative to `src/ssis_migration/`; test fixture paths are
relative to `tests/adapters/tm1/`. No dependency or existing function signature
changed. Existing SSIS IR construction/analysis order, migration_intent, providers,
AI orchestration, generation and acceptance gates remain unchanged. The general
SSIS metadata loader is not used for dimensional metadata.

## Verification and next step

Focused TM1/MDX/intake/schema suite: **307 passed**. The synthetic golden example
has 30 bindings: 27 declaration matches, three unresolved bindings, no catalog
issues and all 24 original dependency requirements retained. No source process,
SQL, command, server connection, AI request or Databricks workload ran.

Full regression: **918 passed, 12 skipped**. Eight skips require the .NET SDK,
which is unavailable in this environment; four are existing live integrations.
The offline wheel build and wheel-only CLI/schema checks passed outside the
checkout, including source evidence, a successful merge of disjoint same-source
catalog files, and the blocked package-adapter boundary. Tracked and new-file diff
checks passed. Changes are local on `GenericAITool-version01`, uncommitted and
unpushed.

Next: integrate these source analyses and bindings into a TM1 adapter and a
source-independent IR/migration contract without fabricating SSIS PackageIR.
Native versioned `.pro` decoding and verified view/process/runtime behavior still
require representative user-supplied exports and execution evidence.

The subsequent [native integration increment](phase8-tm1-native-integration.md)
now registers an explicit file-bundle adapter, native IR v2 and review-only
contracts. Target translation and migration acceptance remain blocked.
