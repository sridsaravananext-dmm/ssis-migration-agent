# Public PowerCenter export: audit and first compatibility increment

Inspected on 2026-10-26 before changing the reader. Upstream:
[Praveen-pa/Informatica-Powercenter](https://github.com/Praveen-pa/Informatica-Powercenter/tree/43a58b9629b767454e5622913d12f53335866ea2/SCD1),
commit `43a58b9629b767454e5622913d12f53335866ea2`. The root MIT license
and the embedded vendor notices must be retained with unchanged source files.
These are existing public exports, not new source examples authored for testing.

Both native XML files declare `Windows-1252`, repository version `189.98`, and
reference the external `powrmart.dtd`. Intake identified PowerCenter correctly,
but the pre-change adapter rejected both encoding declarations before graph building.
The 49 existing XML-adapter/intake-pipeline checks passed before this increment.

| Existing file and entry point | Responsibility and reuse | Smallest required change | Dependencies and risks | Acceptance checks |
| --- | --- | --- | --- | --- |
| `intake/inventory.py`: `describe_file`, `_xml_detection` | Shared file identity and native XML detection | Reuse unchanged; it already detects these original exports | Detection is not semantic support; external DTD must never be fetched | Identify both originals as PowerCenter with exact byte hashes |
| `adapters/xml_export.py`: `XMLExportAdapter._build`, `ExportGraph.finish`, `validate_export_graph` | Shared bounded parser, source snapshots and exact replay | Honor explicit Windows-1252; preserve original encoding and bytes in replay; retain existing UTF-8 graph representation | No guessing or replacement decoding; reject unsupported encodings and BOM conflicts; retain entity/DTD guards and resource limits | Parse/replay both original files, reject altered snapshot encoding, preserve archived UTF-8 graph bytes |
| `adapters/informatica/adapter.py`: `PowerCenterAdapter.classify`, `relationships` | Vendor-specific tags and scoped links | Audit unchanged in this increment; preserve unsupported elements and unresolved links | Mapping and workflow are separate files; broadening tag recognition alone would not implement their semantics | Every original XML element survives; workflow's missing mapping remains explicit |
| `adapters/export_pipeline.py`: `run_export_review`, `validate_export_review`; `migration/native_review.py` | Reusable IR, review contracts and reports | Reuse unchanged | One-file review does not bind dependencies across exports; native translation remains unimplemented | Both originals produce replayable review artifacts; every node/edge remains an obligation; no accepted status or target notebooks |
| `schemas/source_envelope.v2.json`, `schemas/normalized_ir.v2.json` | Versioned source envelope and graph | Reuse extension fields; no breaking schema/interface change | Existing UTF-8 snapshots must remain valid without an encoding field | Existing schema, SSIS and native review regressions pass |

The mapping contains 157 elements, one source, one target, six transformations
(Source Qualifier, Lookup Procedure, Expression, Router, Sequence and Update
Strategy), 26 connectors and nine instances. `GROUP` and `TARGETLOADORDER` are
not implemented by the current structural profile and must stay visible.

The workflow contains 163 elements, one session referring to `m_scd1`, two task
instances and one workflow link, but no mapping definition. Its scheduler,
session-transformation instances and partition declarations also exceed the
current tag profile. Finding the mapping in a neighboring file does not authorize
silently merging it into this single-file review.

Declared behavior also constrains the later translation phase. These are direct
XML values from the pinned mapping, not inferences from the folder name `SCD1`:

| Original evidence (under `MAPPING NAME="m_scd1"`) | Declared value | Translation requirement still unresolved |
| --- | --- | --- |
| `TRANSFORMATION NAME="LKPTRANS"` / `TABLEATTRIBUTE` | Lookup table `target`, condition `ID = lkp_ID`, multiple-match policy `Use Any Value` | Preserve lookup behavior; no unique key or deterministic duplicate tie-break has been supplied. Do not silently choose a new winner. |
| `TRANSFORMATION NAME="RTRTRANS"` / output `GROUP` | `isnull(lkp_DIM_KEY)` and `not isnull(lkp_DIM_KEY) AND (lkp_LOCATION != LOCATION)`; separate default group | Preserve null/comparison semantics, group membership and default routing; verify how the declared connectors consume each output. |
| `TRANSFORMATION NAME="SEQTRANS"` / `TABLEATTRIBUTE` | Start `0`, increment `1`, current `1`, end `9223372036854775807`, cycle `NO`, reset `NO` | Account for sequence state and restart/concurrency behavior; a per-run row number is not established as equivalent. |
| `TRANSFORMATION NAME="UPDTRANS"` / `TABLEATTRIBUTE` | Update Strategy Expression `DD_UPDATE` | Preserve the session's actual update/key/error semantics before selecting a target write strategy. |
| `TARGETLOADORDER` | `target2` and `target` both declare order `1` | Preserve both declarations; XML listing order alone does not establish a serial tie-break. |

The supplied upstream log reports a successful workflow run. It does not supply
input rows, observed output rows or artifact/input/parameter bindings required
for migration equivalence. Do not turn that log into verified semantic coverage.

First increment: import exact originals with license, pinned URLs, sizes and
SHA-256 provenance; fix declared-encoding handling and replay; exercise the real
files through intake, IR, contracts and reports. Do not fabricate dependencies,
input datasets, expected outputs or a replacement combined workflow export.

Next increments require explicit multi-file dependency binding, supported
transformation semantics and corresponding Databricks generation. Actual source
observations and assessed critical rules/weights remain necessary for acceptance.

Completed verification: 59 focused XML/intake checks passed; full regression
1,246 passed and 12 skipped (four live Databricks checks, eight optional .NET SDK
checks). All three prior UTF-8 graphs compare exactly to their pre-change form.
Both public exports passed installed-wheel review and artifact replay after
deleting the temporary original source copies. The documented CLI command also
produces the expected blocked review with explicit capability gaps.
