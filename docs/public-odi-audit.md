# Oracle ODI Smart Export compatibility audit

This page records the legacy compatibility checkpoint. The subsequent
[versioned ODI mapping normalizer](odi-mapping-normalization.md) now handles this
original export for IR/contract review, while keeping the earlier preflight
representation and legacy parser limits unchanged. Databricks generation is
assessed per mapping and remains blocked by unresolved source/target behavior.

Audit date: 2026-10-27. Source: Oracle's UPL-1.0
[ETL Offload sample](https://github.com/oracle/data-warehouse-etl-offload-samples/tree/4f67a1ccf84c6a4ef02720690cdee61af3027f25/CloudAtCustomerODIETLOffload),
pinned to commit `4f67a1ccf84c6a4ef02720690cdee61af3027f25`.
This audit reads the existing export; it does not execute Oracle installation
instructions, Knowledge Modules, SQL or workloads, or author input/output data.

The original ZIP is 207,779 bytes, SHA-256
`a61799c8170b5b576f1bcc46be14c06b56c487befb8a13d988fd6c443ff5626f`.
Its sole member is `ODISmartExport_ETLOffload_BigDataCloud@Customer.xml`,
3,363,246 bytes. The XML declares ODI `12.2.1` and repository version
`05.02.02.07`; these are separate declarations, not inferred versions.
It contains 41,411 elements, 2,495 objects in 45 classes and 38,911 fields.
Maximum element depth is 3. Its original encoding declaration is `ISO-8859-1`.
The shared structural parser accepts UTF-8/Windows-1252 and allows only 10,000
elements. Both encoding and element count block this genuine export from native
IR review. The preflight can inventory the declared ISO-8859-1 bytes without
changing their declaration or extending the structural parser.

| Existing file and interface | Current responsibility / reuse | Required change in this increment | Dependencies, risks and acceptance checks |
| --- | --- | --- | --- |
| `adapters/odi/adapter.py`, `ODIAdapter.classify/relationships` | ODI-specific class/key declarations and references | Reuse the current class table for a compatibility inventory; leave existing IR behavior unchanged | Modern `SnpMapping`, `SnpMapComp`, physical execution and KM classes are absent from the structural table. Counts must preserve unknown classes without claiming support. |
| `adapters/xml_export.py`, `XMLExportAdapter._build/analyze` | Shared bounded XML evidence, graph and exact replay | Reuse byte/encoding limits and declaration checks; retain the 10,000-element IR limit | Simply raising the cap would expand every vendor's graph/contracts and still not interpret the modern classes. Existing XML/SSIS regression results must remain unchanged. |
| `ui.py`, `extract_bundle/bundle_candidates`; `app.py`, native XML configuration | File/ZIP intake and structural-review controls | Add a downloadable ODI preflight before migration; disable an over-limit structural run with an explicit reason | Use the original Oracle ZIP through the real upload flow. No IR/contracts, notebooks, execution or acceptance may be reported for this blocked export. |
| `adapters/export_pipeline.py`, `run_export_review/validate_export_review` | Neutral plans/contracts/reports for parsed native graphs | Reuse unchanged for existing eligible ODI exports | Preflight is compatibility inventory only; it must not fabricate a partial graph or bypass the contract/generation gates. |
| `schemas/`, `validate_document` | Packaged offline schemas | Add a strict compatibility-report schema and original-byte replay validation | Reject altered class counts, versions, eligibility and status even if a caller edits the report. Test installed-package schema access. |

The smallest next phase is therefore bounded, read-only compatibility preflight,
with unchanged licensed upstream bytes and pinned provenance. Its acceptance
checks cover exact upstream hashes, original archive discovery, class/field/tag
inventory and declared versions, visible parser-limit blocking, report replay,
and unchanged eligible ODI/other-platform review. Field values, credentials and
encryption key material are not copied into the inventory report.

## Implemented increment

`adapters/odi/preflight.py`, `inspect_export`, now streams and counts the original
XML declarations with a 16 MiB / 100,000-element / depth-128 inventory budget.
At most 4,096 distinct classes, tag names, field types, admin declarations or
encryption declarations are inventoried. These bounds are separate from IR limits.
The report has its own packaged `odi_export_compatibility.v1.json` schema and
profile `odi_export_compatibility_v1`. `validate_preflight` recomputes all report
claims from unchanged original bytes, including when the file has been relocated.
It does not carry a source snapshot and is not an acceptance artifact.

The original contains 90 objects in nine recognized structural classes and 2,405
objects in 36 unsupported classes. Its seven `SnpMapping` and 19 `SnpMapComp`
objects are among those unsupported declarations. The original `Encryption`,
`SmartExportList` and `Include` elements are also outside the structural tag table.
Counts are declaration inventory, not implementation or semantic coverage.

The workbench displays the original ODI and repository versions separately,
the class inventory, both parser blockers and required next actions. It offers
the JSON compatibility report for both original ZIP and direct XML upload, and
disables structural migration for this export. Existing eligible ODI exports
continue through the unchanged native IR/contract pipeline. The source/IR,
provider and `migration_intent` interfaces have no changes.

Original source/license/documentation bytes are stored with
[pinned provenance](../examples/public/oracle_odi_etl_offload/provenance.json).
See [workbench/CLI usage](../examples/public/oracle_odi_etl_offload/README.md).

Focused verification: **30 passed**, including original-byte provenance, native
ZIP discovery, exact counts/versions, both parser blockers, byte replay after
relocation, report tampering, input limits, unchanged eligible XML review and both
real ZIP/XML workbench uploads. Installed-wheel CLI/schema/replay checks passed
outside the checkout. All six archived PowerCenter version 1/2 analyses/contracts
still replay from the installed package; their source hashes are unchanged.
Full regression: **1,348 passed, 12 skipped** in 512.33 seconds. Four skipped checks
require a live Databricks connection; eight require the unavailable optional .NET
SDK. Execution, source equivalence and real SSIS runtime binding remain unverified.

Modern ODI mapping normalization, dependency resolution, Knowledge Module and
technology semantics, Databricks generation, execution and equivalence are later
work. An inventory is not a completed ODI adapter or a migration contract.
