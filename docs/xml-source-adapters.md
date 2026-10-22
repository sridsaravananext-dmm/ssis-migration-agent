# PowerCenter, BODS and ODI structural profiles

These adapters reuse native IR v2, the shared source envelope, neutral review
planner and existing migration contract bridge. They do not construct a fake
SSIS PackageIR, call an AI provider, connect to a repository or generate a
partially implemented migration notebook.

| Adapter | Accepted signature/profile | Extracted behavior and limits |
| --- | --- | --- |
| `adapters/informatica/adapter.py`, `PowerCenterAdapter` | `POWERMART`; `powercenter_xml_structure_v1` | Typed source/target columns, mapping/transformation instances, scoped definition references, connector data/column links, session mapping references, workflow task/conditional control links. Missing or ambiguous definitions/ports block. Shortcuts and unknown elements block. |
| `adapters/bods/adapter.py`, `BODSAdapter` | `DataServices`, `DIExport`, `DIJob`; `bods_xml_structure_v1` | Bounded DI jobs/workflows/dataflows, transforms, scripts/queries, columns, parameters and datastores. Explicit `sourceId`/`targetId`, `refId` and `datastoreId` links resolve only unique IDs. Link labels do not establish execution order. Other XML encodings and ATL are unsupported. |
| `adapters/odi/adapter.py`, `ODIAdapter` | `SunopsisExport`; `odi_xml_structure_v1` | Selected `com.sunopsis.dwg.dbobj.Snp*` object classes, original fields, declared identity and foreign-key references. Unknown classes, duplicate fields/keys and missing referenced definitions block. SQL dialect, KM execution and package order are not inferred from text, object names or repository IDs. |

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

Limits are UTF-8, 16 MiB, 10,000 elements and depth 128. Entities/internal DTDs,
symlinks and unsupported encoding declarations are rejected. PowerCenter's normal
external DTD declaration is accepted without retrieving or executing the DTD.

`adapters/export_pipeline.py` writes source envelope, normalized IR, migration
plan/contracts, plan binding, validation results and migration report into a new
run directory. Every graph node/edge must remain a contract obligation. Reports
include source format/version/profile, tasks, dependencies, lineage, unsupported
constructs and missing references. The final lifecycle remains BLOCKED because
native target translation and source equivalence are not implemented here.

Examples are explicitly **synthetic structural fixtures**, not production vendor
exports or verified runtime observations:

```bash
python -m ssis_migration.migrate --source examples/informatica/simple_mapping.xml --output /tmp/powercenter-review
python -m ssis_migration.migrate --source examples/bods/structural_job.xml --output /tmp/bods-review
python -m ssis_migration.migrate --source examples/odi/structural_export.xml --output /tmp/odi-review
```

The API automatically detects these file signatures, or accepts explicit platform
confirmation. A conflicting confirmation is rejected. The current structural
path requires a self-contained export; it rejects SSIS-specific dependencies,
procedure arguments, AI requests and target mappings instead of silently ignoring
them. Workbench ZIP discovery and direct uploads use the same adapters.

Before expanding generation, add actual vendor exports with their complete
dependencies, explicit capability rules, and independently expected semantic
fixtures. Real transformation/KM/default/error/transaction behavior is not
established by preserving XML records.
