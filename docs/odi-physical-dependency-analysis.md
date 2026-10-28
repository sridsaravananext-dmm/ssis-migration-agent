# ODI physical-design dependency analysis

Architecture audit: 2026-10-27. This increment uses only the unchanged Oracle
[Smart Export and pinned provenance](../examples/public/oracle_odi_etl_offload/README.md).
No new source packages, metadata, datasets or expected workload rows are authored.

This records the phase-19 increment: behavior version 4, 258 requirements,
physical profile version 1 and candidate version 3. The following
[topology/context increment](odi-topology-context-analysis.md) adds behavior
version 5 and 259 requirements. It retains this physical profile and its report
while binding the same 44 expression fragments to all new requirements. Earlier
recorded profiles remain available for replay.

The original declares 11 physical designs, 48 physical nodes, 25 execution units
and 25 groups. Four mappings have alternative designs. Its 137 directly attached
physical properties plus 16 parent-linked descendants total 153 properties.
Literal `true` declarations include two `TRUNCATE`, three
`TRUNCATE_TARGET_TABLE` and three `OVERWRITE_TARGET_TABLE` properties. Their
presence does not establish effective runtime options or permission to execute.

| Existing file/interface | Current responsibility and reuse | Smallest change and risk | Acceptance checks |
| --- | --- | --- | --- |
| `adapters/odi/native.py` | Frozen graph, typed fields and class/key/GUID links | Reuse unchanged. Additional design-context and execution-group links belong in a separate analysis; graph IDs/edges must not change. | Original graph and older analyses replay exactly; original hashes match. |
| `adapters/odi/behavior.py`, `MappingIndex` | Mapping ownership, scoped lookup and recorded-profile dispatch | Add behavior version 4, keeping versions 1–3 available. No name-based or first-record selection. | Reject unrecognized profiles and source-rehashed omissions; all design requirements enter contracts. |
| New `adapters/odi/physical.py`, schema and `behavior_v4.py` | Design-scoped declarations and dependency requirements | Retain all original fields, explicit references, property parent chains and unscoped records. Validate same-mapping/design boundaries, ambiguous identities and missing definitions. Parent/group links do not prove execution order. | Account for all 11/48/25/25 declarations and 153 properties; preserve alternatives and all eight destructive declarations. Reject altered copied artifacts. |
| `generator/odi/expressions_v2.py` | Contract-bound 44 expression review fragments | Extract the existing builder behind its unchanged public interface; add an explicitly bound candidate profile for behavior version 4. No physical SQL generation. | Previous candidate reports compare exactly; current 44 fragments remain syntax-only and all 89 entries remain visible. |
| `adapters/export_pipeline.py`, workbench | Review artifacts, contracts, blockers and downloads | Publish physical analysis and a concise workbench summary. Preserve blocked generation and unavailable semantic coverage. | Original ZIP/API/workbench flow, installed wheel and source-removed replay; no notebooks, execution or acceptance. |

The existing graph resolves physical references within their declared scopes.
Two additional relationships are present as typed fields: 11 design `IMapRef`
declarations identify same-mapping context references, and 25 unit `IExecUnitGrp`
declarations identify same-design groups. Resolve only exact class/key matches
with explicit scope checks. Preserve missing, ambiguous or conflicting declarations
instead of repairing them or following display names.

Execution-unit KM uses (25) and source-component KM uses (37) lack exported
definitions. Target-component uses include four resolved and 21 unresolved
definitions; eight check-KM uses resolve. These are use counts, not distinct
missing module counts. Logical-schema/context definitions do not establish
Databricks tables, paths, runtime values or a selected physical design.

Whole mapping generation, source/runtime semantics, execution, reconciliation,
criticality/weights and migration acceptance remain unverified. Existing date
SQL and command/option association blockers remain explicit.

## Implemented increment

`odi_12c_mapping_requirements_v4` preserves the earlier expression, command and
option requirements, then adds 11 `ODI_PHYSICAL_DESIGN` requirements, totaling
258. Each design requirement retains the original design fields, physical
nodes, execution units/groups, properties, source locators and scoped reference
records. The export-review requirement holds the physical manifest and any
unscoped records. Missing or ambiguous design owners remain explicit unresolved
designs; they are not dropped or assigned to the first mapping.

The physical artifact profile is `odi_12c_physical_dependencies_v1`. All original
typed fields remain intact, including literal `null`, flags, option values and
parent declarations. Reference records separate missing declarations, nonnumeric
values, missing/ambiguous targets, interface mismatches and cross-scope conflicts.
External definitions require matching class, key and exact GUID. Component/CP
inconsistencies and physical, unit or property-parent cycles become diagnostics.
Parent/group relationships do not establish runtime execution order.

`source_physical_analysis.json` contains all 11 designs, 48 physical nodes,
25 execution units, 25 groups and 153 physical properties. The original has no
unscoped records or consistency diagnostics. Its summary records 48 resolved and
83 missing definition uses, separately from distinct exported reference IDs.
Eight potentially destructive property IDs are listed explicitly, with their
original declarations and scope chains available in the property records.

The strict offline schema and extraction checks validate record uniqueness,
bucket classes/scopes, design ownership, manifest/source bindings, reference
counters and destructive-property accounting. `validate_physical_analysis`
rebuilds the native graph from captured original evidence and recomputes the
report exactly. Altering hashes or internally consistent counts cannot replace
original-source replay.

Analysis limits are 256 designs, 4,096 physical nodes, 2,048 units, 2,048 groups,
4,096 property declarations, 64 ownership/parent-chain levels and 256 reference
candidates. Existing native XML/graph bounds remain unchanged. Limit failures
block analysis instead of publishing a partial inventory.

The workbench's **ODI physical designs and dependencies** panel displays counts
and provides the physical-analysis download. The public pipeline adds optional
`physical_analysis` summary and `source_physical_analysis` artifact fields.
Existing IR, provider, `migration_intent` and review interfaces are preserved.
Behavior profiles 1–3 remain explicitly selectable for archived reviews.

Candidate profile `odi_databricks_expression_candidates_v3` binds the same
44 SQL review fragments and all 89 expression records to the new 258
requirements. The version-2 public candidate builder keeps its signature and
recorded-profile behavior; an internal builder is shared rather than duplicating
rendering and target syntax checks. No physical mapping SQL, notebook, job, DAB
deployment, source command execution, semantic coverage or acceptance is enabled.

## Verification scope

Checks use the original public ZIP and faults applied to copies of its existing
in-memory declarations and review artifacts. They cover counts and every typed
field, alternative designs, exact scope/reference bindings, all eight destructive
flags, property chains, missing/ambiguous owners, cross-scope references, cycles,
budgets, contract accounting and source-removed replay. Tests distinguish a
structurally retained orphan design from a source-verified ownership claim.
No new source workflow, metadata, input dataset or expected workload rows are
authored.

Focused validation passed 27 physical-analysis checks, two additional orphan-owner
regressions, 35 earlier expression checks and the original-ZIP workbench run.
The final wheel's implementation/schema files match the checkout. Installed API,
physical report, contract/candidate and capability-assessment replay passed
after removing its temporary source copy. Archived behavior profiles 1–3 and
their plans/contracts/assessments compare exactly; the earlier 41- and
44-fragment candidate reports also compare exactly and replay without source.
Original archive/member, license and upstream README hashes still match.

The full regression passed **1,435 tests, with 12 skipped, in 1,242.76 seconds**.
Four skips require a live Databricks connection; eight require the optional .NET
8 SDK, unavailable in this environment. All 29 new physical-analysis cases are
included. Execution, source equivalence and migration acceptance remain unverified.

The following [topology/context analysis](odi-topology-context-analysis.md) uses
the same export's five physical schemas, five context associations and 30
connection properties, alongside four logical schemas, two contexts and five
connections. These declarations do not establish an effective run context or
Databricks bindings. Missing KM/type definitions, verified runtime policies and
explicit source/target bindings remain necessary before complete generation.
The 32 datatype references represent 13 identity/GUID/name triples;
datatype definitions are absent and datatype binding is not registered by the
native profile. The 52 unresolved KM references represent 19 distinct
identity/GUID/name triples with absent definitions.
Reference descriptors do not replace the missing behavior definitions.
