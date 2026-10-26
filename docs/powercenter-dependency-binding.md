# Explicit PowerCenter export dependencies

This increment follows the [real-export audit](public-powercenter-audit.md).
The original workflow references `m_scd1`; the separate original mapping supplies
that definition. Both exports declare repository `REP`, version `189`, code page
`MS1252`, database technology `Oracle`, export version `189.98`, folder `scd` and
folder UUID `e1e81ffc-cbf0-4742-bae8-f28639a6849a`.

| Existing entry point | Reused responsibility | Minimal addition and risk | Acceptance checks |
| --- | --- | --- | --- |
| `PowerCenterAdapter.analyze` | One-file XML parsing, typed declarations and local references | Add a separate `analyze_bundle` entry point; preserve the single-file interface and graph bytes | Original one-file reviews stay unchanged |
| `adapters/xml_export.py`: `validate_export_graph` | Exact source replay and schema/evidence validation | Dispatch the registered adapter's bundle replay; reconstruct each original encoding independently | Removing files, changing bindings or recomputing hashes cannot bypass replay |
| `adapters/export_pipeline.py`: `run_export_review` | Neutral contracts/reporting | Include all explicit exports and recheck every file before publication | All 320 original elements and all graph obligations survive |
| `migrate.run_migration`, CLI `--dependency` | Existing file-based intake arguments | Permit PowerCenter XML dependencies only; other native platforms retain their limits | Missing/wrong-format/duplicate paths fail explicitly; no directory auto-binding |
| `ui.run_frontend_migration`, `app.py` | Uploads, ZIPs, explicit source selection and review downloads | Add explicit PowerCenter dependency selection; keep unselected files visible | Uploaded mapping is used only after selection; no AI, notebooks or acceptance promotion |

The new structural profile binds a session's `MAPPINGNAME` only to a unique
mapping with matching export version, repository NAME/VERSION/CODEPAGE/DATABASETYPE
and folder NAME/UUID. Missing identity, conflicting versions and duplicate
definitions are blockers. Folder names alone are insufficient. Original files
are never combined into a fabricated vendor export.

Scope: session-to-mapping declaration links. Shortcuts, external reusable
transformations, session overrides, connections, parameters and runtime semantics
are not resolved by this increment. No ordering, unique keys, input data or
expected outputs are invented. Native target translation and equivalence remain
unimplemented; the final report remains a blocked structural review.

From the repository root:

```bash
python -m ssis_migration.migrate \
  --source examples/public/powercenter_scd1/upstream/SCD1/w_scd1.XML \
  --dependency examples/public/powercenter_scd1/upstream/SCD1/m_scd1.XML \
  --output /tmp/public-powercenter-bound-review
```

Repeat `--dependency` for additional native PowerCenter XML exports. Selection is
explicit; the parser does not search neighboring folders for definitions. In the
workbench, upload dependency exports or choose them from the supplied ZIP/source
folder under **PowerCenter dependency exports to bind**. Nothing is selected by
default. Source-path mode also accepts other readable XML paths, one per line.
Unselected supplied files remain visible and inventoried without being bound.

`PowerCenterAdapter.analyze_bundle(source, dependency_files=[...])` is an additive
adapter entry point; the shared single-file `analyze` interface is unchanged.
The profile `powercenter_bundle_structure_v1` stores one original snapshot per
file, retaining its own encoding, hash and size. The common IR v2 and source
envelope schemas remain unchanged. Every selected export is rechecked before
publishing artifacts. Replay uses snapshots rather than original file paths and
verifies inventory versions against the original XML declarations.

Bounds: 16 exports including the primary source; 16 MiB per export, 64 MiB total,
10,000 total elements, 256 session-mapping references, and 64 candidate mappings
per reference. Invalid file selections, unsupported formats and resource-limit
failures do not publish a partial review. Missing or ambiguous declaration identity
is retained in a complete blocked review. Dependencies must be regular files
without symlink ancestors. Other
native platforms still require their supported self-contained export profiles.

Acceptance uses the unchanged public exports and existing regressions. Negative
checks use duplicate original bytes or altered review artifacts, not newly
authored source packages or expected workload results.

Verification on 2026-10-26: **1,272 passed, 12 skipped** in the full suite
(four live Databricks checks and eight optional .NET SDK checks). The 25 binding
checks include duplicate original exports, missing/conflicting scope in hostile
review artifacts, snapshot/link tampering, resource limits and changes before
publication. A real-export workbench check verifies that upload alone does not
bind a dependency, while explicit selection resolves the link. All five previous
single-file graphs are unchanged. The documented CLI and installed-wheel review
passed; bundle artifacts replay after removal of both temporary source copies.
