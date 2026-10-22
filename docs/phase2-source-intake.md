# Phase 2: offline intake and the SSIS adapter boundary

This phase introduces a shared source-adapter interface and file-based intake.
The SSIS adapter wraps the existing parser → IRBuilder → analyzer sequence;
it does not replace their implementations or change `migration_intent`.
Databricks remains the sole generation target.

Only the SSIS DTSX namespace and `PackageFormatVersion=8` have a registered,
fixture-tested adapter. This is structural format support, not a claim that
every SSIS task is semantically implemented. A missing export version is
review-required; other versions are blocked. Recognized TM1, PowerCenter,
BODS and ODI exports are inventoried and explicitly blocked until their
separate adapters exist. DataStage and Ab Initio remain excluded. MDX is
identified as a distinct language and is never passed through SQL translation.

## Inventory without generation

```python
from ssis_migration.intake import build_intake_manifest

manifest = build_intake_manifest(
    "exports/package.dtsx",
    dependencies=["exports/mapping.json", "exports/procedures.sql"],
    base_path="exports",
    platform="ssis",
    role_overrides={"procedures.sql": "procedures"},
)
```

The manifest records every supplied file's logical path, exact-byte SHA-256,
size, stable file ID, detected/confirmed role, format/version and evidence
references. A file input inventories itself, explicit dependencies and mapping
references; it does not scan unrelated siblings. A directory inventories its
contents recursively. Explicit output exclusions are recorded rather than
silently omitted. Symlinks and unreadable inputs remain explicit findings.

`metadata_file` references in connection mappings use `base_path`, matching the
legacy loader. Their edges are resolved, missing, ambiguous or conflicting;
missing files remain inventory records with null hash/size. No source-system
connections are required. File-dependency resolution does not yet establish
complete cross-package or semantic dependency closure.

Content detection does not equate every JSON file with a mapping. Mixed SQL
retains its DDL/procedure role candidates until the user confirms a role.
Confirmations preserve conflicting detection evidence instead of erasing it.

## Migration interface

Existing `run_migration` arguments and report keys remain available. Additive
arguments are `dependency_files`, `source_platform` and `file_roles`:

```python
from ssis_migration.migrate import run_migration

result = run_migration(
    source="exports/package.dtsx",
    output="generated",
    connection_mapping="exports/mapping.json",
    base_path="exports",
    dependency_files=["exports/reference.json"],
    source_platform="ssis",
    file_roles={"reference.json": "database_metadata"},
)
```

Unmapped database metadata and parameters/configuration/scripts that the
current pipeline cannot consume remain review findings. They are not treated
as applied merely because their files were supplied. The existing SSIS loader
still accepts one explicitly selected mapping, procedure export and DDL export;
multiple candidates are not arbitrarily reduced to the first file.

CLI additions:

```bash
.venv/bin/python -m ssis_migration.migrate \
  --source examples/wwi/DailyETLMain.dtsx \
  --output /tmp/ssis-intake-review \
  --base-path "$PWD" \
  --source-platform ssis
```

Use repeatable `--dependency PATH` and `--file-role PATH=ROLE` for additional
inputs/decisions. `--procedures PATH` and `--ddl PATH` select the corresponding
legacy inputs explicitly. `--source-platform` confirms a platform; it cannot
enable an unimplemented adapter or override an excluded format. The Streamlit
UI provides the same platform/role decisions and retains duplicate uploaded
filenames separately.

## Artifacts, schemas and compatibility

The run report links to a unique `intake_manifest.<run_id>.json`. Each analyzed
package adds `source_envelope.json` alongside the existing `package_ir.json`.
The envelope retains an unchanged `legacy_ir` object, source identity and
diagnostics. Its evidence links refer to hashed source files. The pipeline
checks input hashes before analysis and after generation to reject stale
provenance. Earlier run artifacts remain preserved in separate directories.

`SourceAdapterResult.to_legacy_result()` retains `parsed`, `package_ir` and
`analysis`, and adds `source_envelope`. `get_adapter("ssis")` is the only
implemented adapter. Other registry requests produce explicit unsupported or
excluded errors rather than falling back to the SSIS parser.

Packaged schemas live in `src/ssis_migration/schemas/`:

- `common.v1.json`: file/evidence/diagnostic/edge definitions.
- `intake_manifest.v1.json`: versioned inventory document.
- `source_envelope.v1.json`: compatibility envelope around the existing IR.

`validate_document()` uses JSON Schema 2020-12 with only bundled references;
it does not fetch schemas from the network. `validate_evidence_references()`
rejects duplicate and dangling IDs. The declared `jsonschema` dependency and
package data make these validators available in installed wheels.

These schemas validate the intake/envelope contract, not every field or
semantic behavior in the legacy IR. The four pre-existing empty root schema
files have not been presented as working validators. A fully normalized IR
and a versioned migration-contract bridge are subsequent work.

Intake `READY` means recognized file/format metadata; it does not mean accepted
migration. The SSIS wrapper reports executable types whose behavior is not
fully extracted, including the bundled sample's expression tasks. Intake and
adapter findings feed scoped validation and notebook guards without changing
legacy `plan.unresolved` or `migration_intent`. Weighted semantic coverage,
controlled execution and reconciliation remain unverified/unrun as in Phase 1.

## Changed components and checks

| Component | Purpose |
| --- | --- |
| `intake/` | Offline file inventory, content detection, user decisions and mapping-file dependencies. |
| `adapters/base.py`, `registry.py`, `ssis/adapter.py` | Shared boundary, explicit capability registration and reuse of the existing SSIS chain. |
| `schemas/` package | Version/shape/evidence validation using shipped resources. |
| `migrate.py` | Intake routing, source-envelope artifacts, scoped findings and evidence freshness checks. |
| `app.py` | Platform/role confirmation, complete upload preservation and intake results. |
| `pyproject.toml` | Declared JSON Schema dependency and schema package data. |
| New intake, adapter, schema and pipeline tests; updated UI/readiness tests | Verify legacy output equality, source decisions, blocked inputs, provenance and integration. |

Validation: **280 passed, 4 live Databricks tests skipped**. Diff checks passed.
A wheel was built and its schema resources, intake and adapter were exercised
outside the checkout. Existing SSIS tests and the Streamlit sample flow pass.
No target workloads were deployed or executed. The checked-in malformed
`uv.lock` predates this phase and remains a separate reproducibility issue;
the wheel build and current-instance tests do not claim frozen-lock installs.
