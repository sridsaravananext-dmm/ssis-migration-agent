# ODI 12c mapping normalization

Implementation audit: 2026-10-27. This increment uses the unchanged Oracle
[Smart Export and provenance](../examples/public/oracle_odi_etl_offload/README.md).
No legacy source, source rows, metadata or expected workload results are authored.

The original contains seven `SnpMapping` objects, 19 components, 40 connection
points, 12 connections, 83 attributes, 89 expressions, 54 expression references
and 225 external references. Six mappings connect pairs of datastores/files;
the seventh declares joins and an aggregate. It also contains 11 physical designs,
Knowledge Module references and two `TRUNCATE=true` property declarations.
These are source declarations, not selected execution strategies or verified
Databricks behavior. No physical design is chosen from its name or XML order.

| Existing interface | Reuse / smallest change | Acceptance and risk |
| --- | --- | --- |
| `ODIAdapter`, `XMLExportAdapter` | Preserve the legacy `odi_xml_structure_v1` path; add a separate normalizer for explicitly declared ODI 12.2.1 / repository 05.02.02.07 Smart Exports | Old ODI/BODS/PowerCenter graphs and preflight reports must replay unchanged. Unsupported/ambiguous versions stay explicit. |
| `ExportGraph`, native IR v2 and evidence validation | Reuse neutral graph records; store each direct leaf Object/Field as a typed, evidence-bearing field declaration on its object | Preserve all 41,411 original elements as nodes or folded fields, all original bytes and unknown classes. Use ODI-specific bounds without increasing other vendor limits. |
| `validate_export_graph` | Add registered dispatch for the new profile and original ISO-8859-1 snapshots | Full replay rejects self-rehashed omissions and altered references, IDs, expressions, versions or encoding. No reliance on original source paths. |
| `build_native_review_plan`, contract bridge | Reuse neutral obligation accounting and source-requirement contracts | Every node/edge and mapping requirement remains an obligation. No change to `migration_intent`, providers or SSIS IR flow. |
| `app.py`, native XML upload/reports | Permit the new profile after legacy compatibility preflight; expose mapping analysis and generation blockers | Real ZIP/XML uploads reach review; no notebooks, writes, jobs, deployment, execution or acceptance are claimed for unsupported KM/runtime behavior. |

Normalize only declared identities, class-qualified references, explicit connector
endpoints and expression references. Do not resolve objects by similar names,
repository numbers alone, or `SnpReferencedObject`/cross-reference descriptions.
Retain missing/ambiguous IDs, unverified `null` text, reference validity flags,
raw expressions and all physical/KM/default/error settings for review. Separate
source normalization from Databricks capability decisions.

Generation must account for external definitions, context/physical bindings,
Knowledge Module commands/options, transaction/error and target write semantics.
The `TRUNCATE` declarations cannot be silently replaced with append operations.
Producing source IR or finding direct attribute links does not satisfy those gates.

## Implemented profile

`adapters/odi/native.py` implements `odi_12c_mapping_declarations_v1`.
Dispatch requires one unambiguous Smart Export Admin with `OdiVersion=12.2.1`,
`RepositoryVersion=05.02.02.07` and `IsSmartExportFile=true`, with no conflicting
root version. Other Smart Export versions remain explicitly unsupported by this
profile. The earlier generic ODI structural route is unchanged.

The new profile accepts the original explicit ISO-8859-1 declaration in its own
snapshot/replay path. It has separate bounds: 16 MiB, 100,000 elements, depth 128,
6,000 nodes, 20,000 edges, 256 fields/object and 256 mappings. A leaf Object/Field
retains its exact attributes, declared type, text, tail and XML evidence locator
on its object. Nonfolded elements remain nodes, including unknown classes.
Every source element is accounted for and original bytes remain the replay input.
Existing shared XML encoding/element limits have not been increased.

The unchanged original produces:

- 2,500 nodes plus 38,911 folded typed fields, covering all 41,411 elements;
- 4,760 graph edges, including 12 resolved declared data-flow connections and
  50 declared attribute-reference lineage links;
- nine logical source and seven logical target endpoints from explicit mapping
  connections, plus three intermediate join/aggregate components;
- 92 unresolved external references: 52 KM, 32 datatype, seven column and one
  datastore reference. These counts include repeated references to the same
  object and are not counts of distinct missing definitions;
- 20 retained unsupported object classes. A recognized structural class does
  not establish runtime semantics or target support.

External bindings require matching class, declared numeric key and exact exported
GUID. Reference descriptions and similar names never substitute for definitions.
Connector/lineage links require matching mapping scopes; source validity flags
such as `IsValid=0` stay literal declarations, with no claim of semantic validity.
Literal `null` field text stays text and does not become a runtime SQL NULL or
an invented default. Optional unbound fields remain in their original records.

`adapters/odi/behavior.py` creates `odi_12c_mapping_requirements_v1`: seven mapping
requirements and one export-wide review requirement. They retain components,
connection/attribute/expression references, property nesting, all 11 physical
designs and the two TRUNCATE options. Eight requirements bind into the existing
neutral review contract, alongside 7,260 graph record obligations. The new profile
requires this behavior binding; deleting it and rebuilding an older generic plan
is rejected. Old generic ODI/PowerCenter/BODS plans retain their previous replay.

`generator/odi/assessment.py` consumes validated source requirements/contracts
and produces `odi_databricks_capabilities_v1`, checked against a packaged schema.
Each mapping currently has `UNSUPPORTED` strategy with its concrete blocker IDs,
missing reference declarations, native component/expression counts, physical
designs and destructive-option review. `validate_assessment` replays original
source, plan, contracts and capability decisions. No AI code, notebooks, jobs,
deployment, execution or semantic coverage is produced by this assessment.

The workbench permits the new review after showing the legacy compatibility
inventory. It downloads `source_behavior_analysis.json` and
`target_generation_assessment.json`, and displays **ODI mapping requirements**
and **ODI Databricks generation blockers**. The original source/license bytes
and upstream provenance remain unchanged.

## Generation work and required evidence

Next implementation: native component/expression interpretation and translation,
physical design/KM command and option semantics, context and explicit Databricks
table bindings, write/default/error/transaction strategies, then independently
validated target code. The seven mapping declarations are normalized; complete
semantic normalization and target translation are not implemented by this phase.

For a complete real source, supply a self-contained design-time Smart Export with
the referenced KM definitions, commands/options and dependent datastores/columns,
native datatype metadata, selected source context/design and actual run parameters.
The current ODI route consumes one export; PowerCenter's cross-file dependency
option is not an ODI file-binding interface. Supply actual source input/output
observations for execution/equivalence validation when available. The upstream
sample is not substituted for those observations. Nothing is fabricated to fill
missing definitions, business keys, observations, weights or criticality.

Focused validation: 22 native checks (including the mandatory behavior-binding
guard), 67 existing XML/preflight/workbench checks, and one complete real ZIP
workbench run. Original element/field conservation, source replay after path
removal, strict external binding, self-rehashed tampering, chosen-design/hidden
TRUNCATE rejection, resource caps and generation/acceptance blocking are covered.
Full regression: **1,371 passed, 12 skipped** in 726.09 seconds. Four skips require
a live Databricks connection; eight require the unavailable optional .NET SDK.
These checks do not establish ODI execution, Databricks execution or source
equivalence, or certify real SSIS runtime references.

Installed-wheel API review passed outside the checkout. The new IR, mapping
requirements, contracts and Databricks assessment replay after deleting the
temporary original-source copy. Three pre-existing vendor graphs/envelopes and
the Oracle compatibility report compare exactly; all six archived PowerCenter
version 1/2 analyses/contracts replay from the installed wheel. Original public
source/license hashes remain unchanged. Preserve these recorded profiles when
adding further grammar or generation capabilities; new semantics require an
explicitly versioned contract/capability path with regression validation.
