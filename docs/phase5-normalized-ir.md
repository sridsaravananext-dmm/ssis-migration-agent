# Phase 5: source-independent structural IR foundation

The previous shared boundary wrapped the existing SSIS `PackageIR` without
normalizing its contents. This phase adds a separate version-1 neutral graph
and an SSIS-specific projection. It reuses the existing parser, IR builder,
analysis, contracts and generators. It does not switch the planner to a new
model or register another platform adapter.

The pipeline still builds `IRBuilder().build(parsed)` before
`analyze_package(package_ir.to_dict())`. The adapter additionally projects the
built IR into the neutral graph. The migration runner validates and saves that
graph **before any target generation**. Legacy `package_ir.json`, analysis and
`migration_intent` interfaces remain available.

## Representation and evidence

`normalized_ir.json` has a version, content identity, source/export metadata,
hashed file evidence, typed nodes/edges, diagnostics and explicit capability
gaps. Its structure is independent of a SQL execution engine or Databricks.

| Entity | Representation |
| --- | --- |
| Tasks | Native identity plus an explicit task operation; unknown types are retained. |
| Data flows | Flow nodes linked to their owning task and contained components. |
| Sources, targets, transformations | Exact registered vendor class identities establish structural roles; heuristic labels/descriptions do not. |
| Parameters | Exported namespace, source type and initial value, without conversion or invented scope. |
| Expressions | Supplied text, language and unverified bindings. SQL-looking task values do not establish SQL dialect or direct execution mode. |
| Connections | Native references, with endpoint/schema metadata left unknown when absent. |
| Control flow | Explicit directed links and conditions, including AND/OR evaluation, expressions, result values and the legacy builder's default-origin flags. |
| Data flow and column lineage | Links resolved from native output/input port and column references, preserving original references. |

List order and names do not establish execution order. Duplicate names do not
collapse nodes. Missing/ambiguous native references produce unresolved edges
with null endpoints and diagnostics; no matching name substitutes for an ID.
Repeated port/column identifiers within a component remain ambiguous. Control
cycles are retained and flagged for loop analysis rather than removed.

Each projected entity has contributing-file evidence and a JSON pointer into
the bound legacy IR. `validate_normalized_ir(document, legacy_ir=...)` verifies
those pointers exist, along with schema, graph identities, endpoints, evidence
references, content identity and the legacy-input digest. It does not prove
that upstream parsing captured all source semantics. Native structured
payloads and fields not yet normalized remain in vendor extensions, alongside
the typed graph. The representation does not rely solely on vendor XML.

IDs are deterministic for the same file evidence and input record positions;
this does not promise stable IDs across reordered or changed exports. Neither
content IDs nor file hashes authenticate source semantics or reviewers.

## Integration and compatibility

`SourceAdapterResult` gains an optional `normalized_ir` field after its
existing fields, preserving the original positional constructor. When the
field is present, `to_legacy_result()` adds a `normalized_ir` key while keeping
the original four keys and objects unchanged.

The runner adds:

- `artifacts.normalized_ir` to package reports and manifests.
- A `normalization` summary containing counts, unresolved-edge count,
  capability gaps and explicit `NOT_VERIFIED` semantic status.
- `normalized_ir_build` and `normalized_ir_validation` stages. A completed
  validation stage records structural checking, not semantic acceptance.

Concrete normalization diagnostics join existing source findings before
contract construction, static readiness and notebook guards. Invalid neutral
graphs fail before target generation. Source file hashes are checked again
after projection, as well as at the existing pipeline boundaries.

The acceptance loader supports older outputs without a neutral graph. When a
graph is present, it checks its legacy binding and carries its diagnostics
into static readiness. The evidence inventory now hashes every available
`package_ir.json`, `source_envelope.json` and `normalized_ir.json` in addition
to the existing generation artifacts. Previously captured evidence with a
different inventory requires renewed review; it cannot silently earn credit
for these additional artifacts.

## Limits

This is a structurally normalized compatibility projection. Every node and
the overall graph remain semantically `NOT_VERIFIED`, with review required.
Known gaps include task hierarchy, incremental logic, transaction boundaries,
complete error handling, parameter binding, source schema resolution and
transformation-expression analysis. Existing extraction limitations are
preserved; adding a graph cannot recover source behavior the parser omitted.

The legacy planner/generators continue using their established inputs. No
source or target connections are opened by normalization, no expressions are
executed, and no generated workload is deployed. TM1/MDX, PowerCenter, BODS and
ODI adapters remain future work; accepting their metadata in the neutral
schema does not imply those adapters exist. DataStage and Ab Initio remain
excluded.

## Changed files

| File | Purpose |
| --- | --- |
| `ir/normalized.py` | Shared integrity, graph, evidence-pointer and version validation. |
| `ir/__init__.py` | Export the additive validator. |
| `adapters/ssis/normalizer.py` | SSIS-only mapping and reference resolution. |
| `adapters/ssis/adapter.py` | Build the neutral graph and recheck source bytes. |
| `adapters/base.py` | Optional neutral result without changing existing fields. |
| `schemas/normalized_ir.v1.json`, `schemas/__init__.py` | Packaged offline schema and registration. |
| `migrate.py` | Validate/save/report the graph before generation and retain concrete findings. |
| `validator/acceptance.py` | Bind additional available IR artifacts and retain graph findings. |
| `tests/ir/`, `tests/test_normalized_ir_pipeline.py` | Unit, golden structure, integration, integrity and no-execution cases. |

All source paths above are relative to `src/ssis_migration/` except tests.

## Verification

- Full suite: **423 passed, 4 live integration tests skipped**.
- 41 added cases cover reference ambiguity, preserved defaults/unknowns,
  schema and graph integrity, provenance pointers, input/content binding,
  source freshness, portable golden structure and generation ordering.
- The existing adapter regression still produces identical legacy parsed
  data, `PackageIR` and analysis.
- A built wheel was imported outside the checkout and completed an offline
  sample migration with 192 nodes, 409 edges and 145 column-lineage links.
  Its neutral graph was schema/binding validated and included in acceptance
  evidence. The sample remains `BLOCKED`, semantics `NOT_VERIFIED`, execution
  `NOT_RUN`.
- No live AI call, target workload or deployment ran. Diff checks passed.
