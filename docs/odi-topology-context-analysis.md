# ODI source topology and context declarations

Architecture audit: 2026-10-28. This phase uses the unchanged, licensed Oracle
[Smart Export](../examples/public/oracle_odi_etl_offload/README.md). No source
packages, metadata, input datasets or expected workload rows are authored.

The export already contains 51 topology records: four logical schemas, two
contexts, five connections, five physical schemas, five context associations and
30 connection properties. The last three classes remain unsupported declarations
in the frozen native graph. A separate analysis can use their typed fields
without changing graph identities, edges or earlier review profiles.

There are five exact declared bridges `(logical schema, context, physical
schema, connection)`: `(8,2,7,21)`, `(9,2,48,72)`, `(9,3,58,72)`, `(7,2,6,20)`
and `(20,2,59,4)`. All 25 execution units have logical-schema references. Retaining
all context alternatives gives 34 candidate chains: 25 match the declared
design context and nine use the alternative context for logical schema 9.
A declared/default context is not an established effective run context.

| Existing interface | Responsibility and reuse | Smallest change and risk | Acceptance checks |
| --- | --- | --- | --- |
| `native.py`, `MappingIndex` | Frozen XML/typed evidence and mapping scopes | Reuse unchanged; add analysis keys for physical schemas without changing native class support or graph links. Names and default flags must not become identity bindings. | Original graph and profiles 1–4 replay exactly; original hashes match. |
| `physical.py`, `PhysicalIndex` | Exact typed references, scope checks, original-field evidence and physical-report extraction | Reuse lookup/evidence; extract profile-bound physical-report accounting behind its unchanged version-4 public interface. | Version-4 physical report unchanged; topology requirements cannot masquerade as an older source profile. |
| New `topology.py`, strict schema, `behavior_v5.py` | Source topology declarations, context bridges and execution-unit candidates | Preserve all 51 records, parent/URL declarations, empty/literal-null/undefined values, ambiguous/missing targets and every context candidate. Add one `ODI_TOPOLOGY_REVIEW` obligation, for 259 requirements total. | Account for all original fields, five bridges, 25 unit uses and 34 candidates; reject source-rehashed omissions and fabricated selected contexts. |
| Existing shared expression builder and new candidate profile | Contract-bound target syntax review | Bind the same 44 SQL fragments and 89 entries to all version-5 requirements. No connection, template expansion, physical SQL or notebook generation. | Earlier candidate reports compare exactly; syntax remains unexecuted and unverified. |
| Review pipeline and workbench | Contract/review artifacts, blockers and downloads | Add topology summary and JSON download with context alternatives. No runtime/default context, inherited connection value or Databricks binding is selected. | Original ZIP/API/workbench, installed-wheel and source-removed replay; complete generation stays blocked. |

Connection parent references declare Hive and Spark connections under the
Hadoop connection. They do not establish effective property inheritance. The
30 properties contain 17 nonempty values, 11 empty values and two literal `null`
values; `IndUndefined` is `0` on 23, `1` on two and literal `null` on five.
Preserve original expressions/placeholders without expansion or execution.
Connection URL text references, where available, are opaque source declarations.

Model `LschemaName`/`RevContext` values are names rather than explicit identity
links. Do not bind models or repair missing datastore/column references by names.
Technology/datatype definitions, missing KM behavior, effective runtime policies,
Databricks table/path/parameter bindings and independent observations remain
unresolved. This increment cannot establish semantic coverage or acceptance.

## Implemented increment

Source behavior profile `odi_12c_mapping_requirements_v5` retains the previous
258 requirements and adds one `ODI_TOPOLOGY_REVIEW` on the export root. Its
259 requirements enter the existing plan/contract bridge before target syntax
review. The physical report's public version-4 extractor is unchanged; a shared
private extractor requires an explicit source-profile binding for version 5.
The native graph, its unsupported-class diagnostics and recorded behavior
profiles 1–4 remain unchanged.

The topology profile is `odi_12c_topology_dependencies_v1`. Its 51 records retain
every original typed field and evidence locator. Exact class/key and scoped GUID
lookups preserve missing, ambiguous and conflicting references. Parent cycles,
duplicate keys, duplicate logical-schema/context associations and conflicting
connection-property declarations remain diagnostics. No property inheritance,
default selection, schema-name substitution or technology conversion is applied.

All 25 execution-unit uses retain their original fields, owner/reference
declarations and every supplied association for a bound logical schema. All
34 candidates remain present, including nine that differ from the design's
declared context. Unresolved units and unassignable association records stay
visible. Every candidate has `effective_binding=null`.

Fifteen URL selectors retain three original text records without choosing an
effective URL or interpreting encryption flags. Both `Txt` and `FullTxt` remain
source declarations. Seven fields contain 13 opaque marker occurrences, counting
both text representations; no substitution, SQL parsing, network connection or
source command execution occurs.

The workbench's **ODI source topology and contexts** panel downloads
`odi_topology_analysis.json`; the artifact bundle stores the same document as
`source_topology_analysis.json`. The migration report adds an optional
`topology_analysis` summary and a completed source-analysis stage. Complete
generation stays `BLOCKED`, execution stays `NOT_RUN`, semantic coverage is
unavailable and `migration_accepted=false`.

Candidate profile `odi_databricks_expression_candidates_v4` reuses the existing
expression renderer and target syntax checks. All 44 SQL review fragments and
89 expression entries bind to all 259 source requirements. Date SQL, physical
mapping SQL, executable notebooks, jobs and deployment are not enabled by these
declarations. Provider and `migration_intent` interfaces are unchanged.

## Bounds and verification

Bounds cover 4,096 topology records, 2,048 execution units, 8,192 context
candidates, 64 parent levels, 2,048 source text records, 65,536 characters per
text field, 4,096 URL selectors, 4,096 copied text records, 4 MiB of copied text
characters, 32,768 copied text fields, 200,000 emitted evidence references and
4,096 opaque markers. Aggregate budgets are checked before expanded copies.
The native XML and graph bounds remain unchanged. Exceeding a budget fails
analysis before publishing a partial report.

Thirty-five focused topology cases and the original-ZIP workbench run passed.
They use only the existing public export and faults in copies of its declarations
or review artifacts. Checks cover all records/candidates, source evidence,
default/alternative contexts, null/empty/undefined fields, opaque text, missing
and ambiguous references, parent cycles, contract accounting, schema guards,
source-removed replay and output budgets. No new source workflow, metadata,
input dataset or expected workload rows are authored.

Full regression: **1,470 passed, 12 skipped in 1,185.93 seconds**. All 35 new
topology cases are included. Four skips need a live Databricks connection; eight
need the optional .NET 8 SDK, unavailable in this environment. Execution,
source equivalence and migration acceptance remain unverified.

The installed wheel's implementation and schema files match the checkout.
Its public API and topology/physical/contract/candidate/capability-assessment
replay pass after removing the temporary original source copy. Canonical hashes
captured from the pre-change version compare exactly for behavior profiles 1–4,
their plans/contracts/assessments and candidate profiles 1–3. Version 5 preserves
the original physical report exactly. Original archive/member, license and
upstream README hashes match their pinned provenance.

The next semantic step can join data flow and lineage to the 79 exact
attribute/column bindings and 82 datatype-descriptor uses already supplied.
Corresponding length, scale and required/mandatory strings agree on the 79
column-bound attributes; this is a declaration comparison, not runtime proof.
Four derived attributes have no column reference. In particular, derived `AMT`
attribute 191 declares descriptor `GENERIC_SQL.BIGINT`, length `10`, scale `2`
and literal-null precision. Keep this combination explicit instead of repairing
its type, filling precision or truncating decimals.

Missing SA datastore/column descriptors have no incoming use through registered
reference fields; active component 47 binds the separately identified
`SALES_ANALYSIS` datastore. Preserve all unresolved descriptors without substituting
names or declaring them globally irrelevant while unsupported classes/templates
remain unverified. Full DDL/datatype definitions, actual file-format policies,
explicit target bindings and exported generated scenario SQL/resolved run
parameters can supply further evidence as files; no server connection is needed.
