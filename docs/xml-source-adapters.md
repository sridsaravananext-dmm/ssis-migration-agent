# PowerCenter, BODS and ODI structural profiles

These adapters reuse native IR v2, the shared source envelope, neutral review
planner and existing migration contract bridge. They do not construct a fake
SSIS PackageIR, call an AI provider, connect to a repository or generate a
partially implemented migration notebook.

| Adapter | Accepted signature/profile | Extracted behavior and limits |
| --- | --- | --- |
| `adapters/informatica/adapter.py`, `PowerCenterAdapter` | `POWERMART`; `powercenter_xml_structure_v1`; explicit files use `powercenter_bundle_structure_v1` | Typed source/target columns, mapping/transformation instances, scoped definition references, connector data/column links, session mapping references, workflow task/conditional control links. Explicit cross-file session/mapping binding requires matching repository, folder UUID and export version. Missing or ambiguous definitions/ports block. Shortcuts and unknown elements block. |
| `adapters/bods/adapter.py`, `BODSAdapter` | `DataServices`, `DIExport`, `DIJob`; `bods_xml_structure_v1` | Bounded DI jobs/workflows/dataflows, transforms, scripts/queries, columns, parameters and datastores. Explicit `sourceId`/`targetId`, `refId` and `datastoreId` links resolve only unique IDs. Link labels do not establish execution order. Other XML encodings and ATL are unsupported. |
| `adapters/odi/adapter.py`, `ODIAdapter` | `SunopsisExport`; legacy `odi_xml_structure_v1`, plus versioned `odi_12c_mapping_declarations_v1` for explicit 12.2.1 / repository 05.02.02.07 Smart Exports | Legacy selected Snp classes/keys remain unchanged. The new profile normalizes mapping/component/connection/attribute/expression declarations, preserves typed fields and resolves only explicit class/key/GUID references and scoped data-flow/lineage links. Unknown classes, missing definitions and physical/KM semantics stay blocked. No SQL dialect, execution order or write policy is inferred from names. |

The exact class/tag/key tables in each adapter define the supported structural
profile. A recognized tag is not a supported target-generation capability.
Other vendor versions may be structurally inspected, with their version retained
and an explicit `EXPORT_VERSION_SEMANTICS_UNVERIFIED` diagnostic. No vendor
version has been certified for semantic equivalence by these synthetic examples.

Shared `adapters/xml_export.py` provides bounded XML loading, evidence snapshots,
typed graph records and containment links. Every element retains its attributes,
text, tail and indexed source locator. Every declared vendor relationship is a
separate typed edge. Unsupported elements become retained requirements and error
diagnostics. XML is not the sole representation of the export's behavior.
Source bytes remain available for exact replay; recomputing a graph hash cannot
authorize omitted nodes, altered conditions or removed relationships.

Legacy shared XML limits are UTF-8 or explicitly declared Windows-1252, 16 MiB, 10,000 elements and
depth 128. No encoding is guessed and invalid bytes are not replaced. Windows-1252
snapshots carry their original encoding; historical UTF-8 snapshots retain their
existing shape and exact replay behavior. Entities/internal DTDs, symlinks, BOM
conflicts and unsupported encoding declarations are rejected. PowerCenter's normal
external DTD declaration is accepted without retrieving or executing the DTD.

`adapters/export_pipeline.py` writes source envelope, normalized IR, migration
plan/contracts, plan binding, validation results and migration report into a new
run directory. Every graph node/edge must remain a contract obligation. Reports
include source format/version/profile, tasks, dependencies, lineage, unsupported
constructs and missing references. The final lifecycle remains BLOCKED because
native target translation and source equivalence are not implemented here.

The original examples below are **synthetic structural fixtures**, not production vendor
exports or verified runtime observations:

```bash
python -m ssis_migration.migrate --source examples/informatica/simple_mapping.xml --output /tmp/powercenter-review
python -m ssis_migration.migrate --source examples/bods/structural_job.xml --output /tmp/bods-review
python -m ssis_migration.migrate --source examples/odi/structural_export.xml --output /tmp/odi-review
```

The API automatically detects these file signatures, or accepts explicit platform
confirmation. A conflicting confirmation is rejected. The current structural
path rejects SSIS-specific dependencies, procedure arguments, AI requests and
target mappings instead of silently ignoring them. PowerCenter can now use
[explicit XML dependency files](powercenter-dependency-binding.md); BODS and ODI
still require self-contained exports. Workbench ZIP discovery and direct uploads
use the same adapters.

Before expanding generation, add actual vendor exports with their complete
dependencies, explicit capability rules, and independently expected semantic
fixtures. Real transformation/KM/default/error/transaction behavior is not
established by preserving XML records.

The [public PowerCenter SCD1 exports](../examples/public/powercenter_scd1/README.md)
are now available unchanged with upstream provenance and license. Both native
Windows-1252 declarations parse and replay; the mapping retains 157 elements and
the workflow 163. Unknown elements remain blockers, and the workflow's separate
mapping dependency is not silently resolved by single-file analysis. See the
[audit and next increment](public-powercenter-audit.md).

When the mapping is explicitly selected as a dependency, the native bundle profile
resolves that declaration link, retains all original files and graph obligations,
and checks every snapshot during replay. This does not implement transformation
semantics, connections, runtime execution or target generation.

PowerCenter reviews also produce a separate
[declared-behavior analysis](powercenter-behavior-analysis.md). Its native
expression syntax, router field links, sequence/lookup requirements and session
policies enter contracts as unverified source requirements. Structural profiles
and previously generated generic contracts retain their representations. Effective
configuration, physical connections, runtime equivalence and target translation
remain unimplemented.

PowerCenter's latest behavior profile now includes
[port-default analysis](powercenter-port-defaults.md). It records all original
default declarations and the two explicit sequence error expressions. Replay uses
the recorded behavior version, preserving earlier version 1 reviews.

[Version 3 configuration/binding analysis](powercenter-configuration-bindings.md)
now maps selected configuration and instance contributions, and connection owner
contexts. It retains unknown precedence, physical metadata and runtime values as
unresolved. Both earlier behavior versions remain replayable.

The [original Oracle ODI Smart Export](../examples/public/oracle_odi_etl_offload/README.md)
now has a separate, bounded compatibility preflight. It inventories the original
41,411 elements, 45 object classes, declared ODI/repository versions and explicit
ISO-8859-1 encoding. Both original ZIP and XML workbench upload provide a JSON
report and explain why the earlier structural profile is ineligible. The larger preflight inventory
budget and ISO-8859-1 inventory support do not change the XML IR parser's original
encoding/element limits. Class/KM semantics, dependencies and target generation
remain pending; see [the audit](public-odi-audit.md).

The [ODI 12c normalization profile](odi-mapping-normalization.md) now accepts the
original Oracle export for review. It retains explicit ISO-8859-1 bytes in its
own replay path, folds leaf Object/Field declarations into typed object records,
and uses separate limits: 16 MiB, 100,000 elements, 6,000 nodes, 20,000 edges,
256 fields/object, 256 mappings and depth 128. Legacy XML limits are unchanged.
All seven mappings enter source requirements and contracts; a separate Databricks
assessment reports why code generation remains unsupported. No partial notebook
is emitted for missing KM definitions, unresolved metadata or write semantics.

ODI source behavior version 2 added [bounded native expression syntax and
procedure/KM declarations](odi-expression-km-analysis.md) without changing the
original graph profile. Its 247 source requirements preserve all expression,
command, option and association declarations. A separate target artifact contains
41 SQL syntax review fragments and explicit entries for untranslated source
records. Source native text is not passed to a SQL parser, templates are not
expanded, and these fragments enable no workflow execution or acceptance.

[Source behavior version 3](odi-date-window-analysis.md) extends native syntax
analysis to 45 expressions, including the original date conversion and three
windows. The candidate version-2 artifact contains 44 SQL review fragments from
all 89 source records. Date SQL is withheld pending dialect/calendar, malformed
input, substring-index and target-format rules. Window clauses retain omitted
defaults and the exact `ROWS 3 PRECEDING` form; runtime defaults and ordering
uniqueness remain unverified. The native IR and 247 requirement count are
unchanged. Earlier behavior/candidate profiles retain explicit replay dispatch;
the complete workflow remains blocked with no execution or semantic credit.
