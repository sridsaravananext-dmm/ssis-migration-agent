# Phase 8, fifth increment: TM1 adapter, native IR and review contracts

This is the source-review checkpoint. The subsequent
[Databricks candidate checkpoint](tm1-databricks-candidate.md) adds opt-in bounded
generation through a separate target contract; the review path below is preserved.

## Audit and chosen boundary

The previous increment supplied dimensional metadata and process binding. This
increment connects existing MDX, rule/feeder and TurboIntegrator analyses to the
shared plan/contract interfaces. It deliberately stops at a blocked review
package: no faithful TM1-to-Databricks translator is implemented yet.

The pre-change focused adapter/IR/migration/schema/intake suite passed **406
tests**. Inspection found two SSIS compatibility assumptions that cannot be
applied to TM1 without inventing an SSIS PackageIR.

| Existing file/function | Reuse or change | Dependency/risk | Acceptance check |
| --- | --- | --- | --- |
| `adapters/base.py`, `SourceAdapterResult` | Reuse shared protocol/result. Native results carry `package_ir=None`; legacy conversion rejects native v2 envelopes. | Keep the existing empty legacy constructor and SSIS return keys working. | SSIS regression and native conversion tests. |
| `schemas/source_envelope.v1.json`, `normalized_ir.v1.json` | Keep SSIS compatibility v1 unchanged. Add native envelope/IR v2 with a source-analysis digest. | Do not disguise TM1 input as a legacy-IR projection or hash it as SSIS. | Versioned schema tests; v2 explicitly refuses a supplied legacy IR. |
| `ir/normalized.py` | Reuse common graph identity, evidence and endpoint validation, with version dispatch. | Common validation establishes structure; adapter-specific replay must establish preservation of source input. | Graph replay and rehashed omission/guard/evidence tests. |
| `migration/normalized_plan.py` | Reuse exact graph accounting; add v2 source digest scopes. | Package review carriers account for declarations without establishing execution ownership/order. | Every node/edge preserved exactly once in contracts. |
| `migration/contract_bridge.py` | Reuse the migration_intent compatibility bridge and contract validators. | Contracts bind generic planner snapshots; a planner snapshot is not a fabricated PackageIR. Native mode must not grant translation capabilities. | Native REVIEW_ONLY contracts, empty generation languages, unchanged SSIS contracts. |
| `migrate.py`, `validator/acceptance.py` | Route explicit TM1 bundles to a review pipeline; refuse acceptance of review-only output. | No fallback into SSIS/SQL generation, no notebook/job/DAB placeholders or false acceptance. | Pipeline, provider/generator guards, acceptance refusal and output preservation tests. |

Paths in production tables are relative to `src/ssis_migration/`.

## Explicit file bundle

The registered `TM1Adapter` accepts only a version 1.0
`tm1_source_bundle_v1` JSON manifest. This is an explicitly declared interchange
format, not a native IBM export decoder. The manifest provides:

- TM1 platform declaration, bundle name and declared source/export version or
  `null`.
- Up to 16 source units with unique IDs, exact language roles, relative/absolute
  paths, SHA-256 hashes and optional applicable source-cube/process context.
- Up to 32 explicitly supplied metadata files with paths and SHA-256 hashes.

MDX and rules units name their plain source files. TI units name the existing
versioned section-file manifest, which separately maps Prolog, Metadata, Data and
Epilog files. Rule-cube context remains explicit; it is never inferred from a DB
lookup or filename. A TI process context cannot override a conflicting source
declaration. Bundle version conflicts with supplied source/metadata are errors.

File roles come from the manifest; filenames, upload order and section-like
comments do not establish roles or execution order. Relative paths resolve
against their own declaring manifest. Native `.pro` layouts, other proprietary
formats and undeclared dependencies are not guessed. Excluded DataStage/Ab Initio
files and conflicting detected platforms cannot be relabeled as TM1.

All referenced inputs are inventoried and hashed. IDs use a common bundle root,
so identically named/identical-content files in different directories retain
distinct evidence. Unrelated neighboring files are not discovered implicitly.
Regular-file/symlink checks, declared hashes and final freshness checks prevent
using stale evidence. Missing/non-regular files, malformed declarations and hash
conflicts fail intake explicitly rather than substituting empty source.

The generic intake recognizes the explicit bundle header. Complete referenced-file
inventory and format validation happen in the TM1 adapter; header detection alone
does not establish support for the source behavior.

## Native source envelope and IR v2

SSIS still returns envelope/IR v1 and its existing PackageIR. TM1 returns
`SourceAdapterResult` with a native v2 envelope, `normalized_ir` v2 and
`package_ir=None`. `to_legacy_result()` remains available for old SSIS callers;
it explicitly rejects a native result. No SSIS parsing, IRBuilder or
analyze_package call occurs for a TM1 bundle.

V2 provenance uses `evidence_scope: SOURCE_ANALYSIS` and
`source_analysis_sha256`. It has no `legacy_ir_sha256`. Existing common node and
edge structure is reused; REQUIREMENT/REFERENCE node categories and REQUIRES,
DECLARES_EFFECT and DECLARED_RELATION edges preserve source requirements without
pretending they are runtime data or job dependencies.

The common v2 schema is source-independent. TM1-specific parsing and preservation
validation stay in the TM1 adapter. Its extension holds a compact bundle analysis
with original source snapshots, parsed reports, shared metadata inputs and
metadata-binding results. Metadata is stored once in that bundle, rather than
copied into each binding result. The graph represents actual structured behavior;
the attached source snapshots are not its sole representation.

| Source fact | Neutral representation and limitation |
| --- | --- |
| Supplied source files | Collection/unit TASK containment. File grouping is not execution order. |
| TI sections | Distinct section tasks with declared iteration scope and nominal entry/exit nodes. Metadata/Data remain separate source-dependent passes. |
| Assignments, IF/ELSEIF, WHILE, controls and invocations | Ordered transformations and expression trees. Guard vectors, operand/argument positions, branch containment and loop back-edges are preserved. |
| ItemSkip and other early transfers | CONTROL_FLOW edge with unresolved target, plus explicit diagnostics. No assumed successful exit or next-record target. |
| Rule calculations/directives | Ordered cell-rule/configuration transformations, qualifiers and selector/expression nodes. No precedence or cell evaluation is inferred. |
| Feeders | DECLARED_RELATION with sparse-cell activation meaning, never ETL row movement. |
| MDX | Query task, cube source, ordered axes, NON EMPTY flags, member/set/tuple expressions and slicer context. No SQL flattening or guessed cell result. |
| Cube reads/writes and output calls | Declared SOURCE/TARGET call sites and effects with original evidence. Dynamic target expressions remain unresolved. External SQL is not assigned a guessed read/write meaning. |
| Metadata | Typed dimension, hierarchy, member, cube, subset and process-signature declarations. Consolidation relations retain exact weight spellings; no aggregation is evaluated. |
| Parameters and variables | Parameter nodes and scoped symbol declarations retain declared types, required/default fields, datasource-column roles and unverified runtime values. |
| Datasources/columns | Connection descriptors, explicit NONE declarations and source-column definitions. A descriptor does not establish an open connection or source record availability. |
| Missing dependencies/bindings | Explicit requirement/reference nodes and source/metadata file pointers; diagnostic and runtime requirements remain visible. |

All nodes remain `NOT_VERIFIED`. RESOLVED edge endpoints mean known graph
identities, not source-server validity, completed execution or equivalent target
behavior. No cross-unit execution, process completion or column lineage is
invented from matching names.

## Replay and contracts

`validate_bundle` reconstructs MDX/rule/TI reports from preserved original bytes
and re-derives metadata bindings. TI section roles, raw-byte BOM differences,
declared hashes and complete file consumption are checked. Rehashing an edited
AST cannot replace a source cube or remove behavior while retaining the old
source text. Native graph validation reconstructs the expected graph and compares
every node, edge, guard, declaration and requirement.

The source-neutral native planner creates one review step per explicitly marked
analysis unit, using neutral node IDs. No execution dependencies are guessed from
file order, naming or containment. Existing graph-to-plan accounting retains
package declarations on a review carrier when they do not have an implementing
unit owner. Carrier assignment is accounting only.

Every node and edge becomes a preservation obligation exactly once through the
existing migration contract bridge. `migration_intent` remains available as the
compatibility interface; native review steps do not invent SQL/procedure intent.
The contract's `legacy_step` field is the existing generic planner snapshot, not
an SSIS PackageIR or XML projection. Source evidence and scoped neutral records
provide the actual preservation obligations.

Native contracts retain unknown criticality/weights and NOT_VERIFIED coverage.
They use `execution_mode: REVIEW_ONLY`, deployment DISABLED and an empty target
generation-language list. This is an additive v1 contract-schema extension; old
GENERATE_ONLY SSIS contracts retain their existing language requirements and
behavior. Native scopes cannot claim target generation capabilities by editing
and rehashing a contract.

The capability rule is explicit: no registered faithful native translator means
REVIEW_REQUIRED; incomplete source analysis or inconsistent metadata means
UNSUPPORTED. No step becomes complete because it has a contract. Target dispatch,
operation/AI preparation and provider orchestration reject native review scopes
before invoking translators or a provider. Existing SSIS paths remain available.

## Entry points and output

```python
from ssis_migration.adapters import get_adapter
from ssis_migration.migrate import run_migration

result = get_adapter('tm1').analyze('supplied_bundle.json')
graph = result.normalized_ir  # Native v2, never a fabricated SSIS PackageIR.
report = run_migration('supplied_bundle.json', 'review_output', source_platform='tm1')
```

```bash
.venv/bin/python -m ssis_migration.migrate \
  --source examples/tm1/bundle/manifest.json \
  --source-platform tm1 \
  --output /tmp/tm1-review-results
```

The shared entry point also detects an explicit bundle header when platform is
omitted. Directories and arbitrary `.mdx`/`.rux`/`.pro` files are not silently
assembled into a bundle. SSIS-specific external metadata arguments, base-path
overrides, target catalog and AI/execution requests are rejected for this native
review flow; dependencies must be explicitly declared in the bundle.

Each run creates a unique output subdirectory and writes seven review artifacts:
source_envelope.json, normalized_ir.json, migration_plan.json,
migration_contracts.json, plan_ir_binding.json, validation_results.json and
migration_report.json. Existing files are not overwritten. No package_ir.json,
target notebooks, jobs or DAB configuration is fabricated. The current Streamlit
SSIS upload flow is unchanged; native bundle review is available through the API
and shared CLI.

The report inventories supplied files, unit parser/binding status, graph and
contract accounting, selected strategies, unresolved source diagnostics and
required human actions. Final status/lifecycle remain BLOCKED, migrated/generated
counts are zero and acceptance is false. Structural check counts stay separate
from semantic, execution and data-equivalence coverage, which remain unknown or
NOT_RUN. The 80% minimum semantic target and mandatory gates are not satisfied by
graph preservation. The existing acceptance loader explicitly refuses a native
review package even if fake notebook/job files are added.

## Changed modules and tests

| File | Responsibility |
| --- | --- |
| `adapters/tm1/bundle.py` | Explicit file roles, source snapshots, strict hash/freshness checks and replay. |
| `adapters/tm1/normalizer.py` | Typed native source/metadata graph and complete preservation validation. |
| `adapters/tm1/adapter.py`, `adapters/registry.py`, `adapters/base.py` | Bounded registered adapter and guarded native result. |
| `migration/native_review.py` | Source-neutral review plan and exact contract accounting. |
| `migration/normalized_plan.py`, `contract_bridge.py` | Native scopes and REVIEW_ONLY contract/generation guard, preserving v1 behavior. |
| `ir/normalized.py`, `schemas/__init__.py` | Native version dispatch and bundled resource selection by filename. |
| New v2 envelope/IR and TM1 bundle schemas; `migration_contract.v1.json` | Native provenance and explicit review capability boundary. |
| `adapters/tm1/pipeline.py`, `migrate.py`, `validator/acceptance.py` | Shared entry routing, separate review outputs and acceptance refusal. |
| `intake/inventory.py` | Detect explicit TM1 bundle headers without enabling arbitrary native formats. |
| `tests/adapters/tm1/test_native_bundle.py`, `tests/test_tm1_review_pipeline.py` | Replay, control-flow/declaration preservation, protocol/registry, exact accounting, generation blocking, file identity, freshness and output checks. |
| `tests/adapters/test_ssis_adapter.py` | Remove TM1 from the unimplemented-adapter expectation; other roadmap/excluded platforms remain blocked. |
| `examples/tm1/bundle/`, native golden summary | Synthetic explicit bundle and portable expected structure. |

No dependency was added. V1 SSIS parsing → IRBuilder → analyze_package order,
provider interfaces, existing migration_intent and generation behavior remain
unchanged. No other platform adapter was implemented.

## Verification and remaining work

Focused adapter/IR/migration/schema/intake/native-pipeline suite: **451 passed**.
The synthetic example contains three structured source units and nine supplied
files, with 254 neutral nodes and 394 edges. All **648** graph records occur
exactly once in contracts. Six scoped parameters, two consolidation links and a
separate feeder relation remain explicit. Source metadata is intentionally
incomplete; no missing member/cube/version was filled in to make the fixture pass.

Full regression suite: **963 passed, 12 skipped**. Eight skips require the .NET
SDK, unavailable in this environment, and four are existing live integrations.
The offline wheel build and final wheel-only API/pipeline/schema checks passed
outside the checkout, including all 648 native obligations, generation/acceptance
refusal, legacy optional-scope compatibility and SSIS envelope/IR v1. Tracked and
new-file diff checks passed. No source process, command, SQL, AI request, target
workload or deployment ran.

Changes remain local on `GenericAITool-version01`, uncommitted and unpushed.

Next: explicit capabilities and bounded, independently validated Databricks
translation for supported behavior. Native export decoding, actual process/view
definitions, runtime lifecycle, multidimensional semantics, criticality/weights,
controlled execution and reconciliation still require supplied source evidence.
Preservation accounting does not solve those pending translation/equivalence
requirements.
