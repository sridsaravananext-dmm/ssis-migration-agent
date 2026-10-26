# Developer workbench

PowerCenter runs now include a **PowerCenter behavior requirements** panel and a
direct analysis JSON download. The declared transformations, router/sequence/lookup
requirements and session/writer policies retain source evidence and unresolved
items. A parsed expression or a bound mapping remains unverified semantically;
these reviews do not generate PowerCenter migration notebooks.

The Streamlit frontend runs existing SSIS migrations, bounded TM1 Databricks
generation, and PowerCenter/BODS/ODI XML structural analysis. Native XML runs
produce IR, declared dependency links, review contracts and reports; they do not
generate vendor migration notebooks. Deployment, persistent data publication and
migration acceptance are not enabled by choosing a platform or layer. Human
Databricks review is deferred while offline implementation continues.

## Start or update the app

Use Python 3.10 or newer from the repository root:

```bash
python -m pip install -r requirements-app.txt
python -m streamlit run app.py --server.port 8000
```

Both requirements entry points install the project's declared dependencies,
including the frontend, schema validation and language parsers. For an existing
Databricks App, publish this code and rebuild/redeploy that App to load the new
frontend. The existing app.yaml still launches Streamlit on port 8000. Nothing in
the workbench automatically redeploys the app or generates credentials.

Source and artifact paths refer to files readable/writable by the app process.
Use uploaded files for laptop data and mounted `/Volumes/...` paths when the app
has the required Unity Catalog permissions. Workspace notebook folders are a
separate setting; `/Workspace/...` is used in job templates, not as an artifact
filesystem directory. Blank workspace/cluster settings remain explicit required
variables in job/DAB templates; no cloud hardware configuration is guessed.

`/Workspace/...` is rejected for **Artifact destination path** and **Target data
path**. Use it only for **Destination workspace folder**. In a Databricks App,
keep the default `/tmp/...` artifact folder and download the ZIP; leave the data
path blank during preview. The **Use app temporary artifact storage** button
resets the artifact field without changing the notebook workspace destination.
App filesystem permission failures report the affected artifact folder and how
to choose a writable destination instead of attempting workspace publication.

## First run from the browser

1. Select **Use TM1 sample bundle** under **Package source**. The sample contains
   synthetic TI, MDX and constant-rule sources, full example metadata, a target
   mapping and four explicit expected-result cases.
2. Set **Target catalog**, **Bronze/Silver/Gold schema**, **Target layer**,
   **Destination workspace folder** and optionally **Existing cluster ID**.
3. Choose a writable **Artifact destination path**. The app creates a unique run
   folder beneath it. The separate **Target data path (proposed)** is recorded in
   notebook parameters and review configuration; a preview does not publish data.
4. Leave **Notebook input mode** at `fixtures` for the first test. In **Runtime
   bindings**, inspect cell snapshot/datasource tables, source record order,
   explicit parameter JSON and proposed output tables. Table mode requires every
   required table binding; blank names are never invented.
5. In **Run Migration**, click **Run Migration**. The source adapter builds IR,
   source/target contracts, capability decisions and validated candidate files.
6. Download all artifacts, or download `Migration.ipynb`, `Validation.ipynb` and
   `DATABRICKS_REVIEW.md` directly. Browse contracts, notebooks and job templates
   in **Artifacts**. Review local fixtures and the manual run steps under
   **Databricks Validation**.
7. Import the generated notebooks into your Databricks workspace and attach
   Python/PySpark compute. Run `Validation` first and save its output, then inspect
   the migration preview. Actual Databricks execution and source/data equivalence
   remain pending until you perform those checks.

Changing form values does not modify the last completed run. Generate again to
apply new settings; its report and downloads retain the exact run configuration.
Failed attempts retain previous completed-run downloads with an explicit failure
message. BLOCKED native reports never appear as successful generation.

## Your own TM1 source exports

Choose **Upload a source bundle (.zip)** or **Use source path**. A ZIP must keep
relative file paths and contain an explicit `tm1_source_bundle_v1` manifest, its
referenced MDX/rule/TI files and metadata. For TI, the manifest must identify all
four section files with hashes. Raw proprietary `.pro` layouts are unsupported.

Select the source manifest explicitly when several source packages are present.
Include a `tm1_databricks_mapping_v1` target mapping in the ZIP, or upload that
JSON separately. Multiple mappings require a selection. The mapping's source
bundle SHA-256 must match the selected manifest exactly. See the downloadable
TM1 example bundle in the sample mode and [the source bundle and target mapping
examples](../examples/tm1/databricks_demo/).

The interface allows explicit input-table and process-parameter overrides and
proposed output table names in the chosen namespace. It preserves the original
mapping and records an effective mapping used by the target contract. Source
hashes, metadata roles, semantic declarations and independently supplied fixture
expectations are preserved. Missing metadata or unsupported logic still blocks
candidate generation; a UI input is not evidence of source equivalence.

ZIP extraction preserves source bytes and rejects traversal, links, colliding
paths, encrypted archives and oversized inputs (64 MiB / 256 entries). Manifest
and TI section references must stay within the supplied source folder. Additional
unreferenced files are listed explicitly and remain in the uploaded-input
inventory; they are not silently added to source execution semantics.

## SSIS compatibility

Use the existing single-package upload and dependency role controls, the WWI
sample, a source path, or a ZIP. ZIP dependencies retain their relative paths.
Confirm ambiguous metadata/procedure/mapping roles instead of selecting an
arbitrary first file. **Metadata reference base path** controls relative
connection-mapping references.

When uploaded metadata filenames differ from the mapping references, use
**Connection metadata bindings**. Each connection shows its role, database and
original reference. Select the actual uploaded file in **Metadata file for
connection 1**, **Metadata file for connection 2**, etc. The app never assumes
that a name such as `database_metadata_wwi (1).json` belongs to a particular
connection. Missing references prevent generation until resolved.

Explicit replacements produce an effective connection mapping while preserving
the original mapping bytes, uploaded input inventory and a binding audit with
source/selected paths and SHA-256 hashes in the run's `configuration` folder.
Only the effective mapping is used for intake, so obsolete original references
do not produce duplicate missing-dependency or conflicting-mapping findings.
Choosing a metadata file does not fabricate metadata or establish equivalence.

The selected catalog initializes notebook `_catalog` and job parameters. Layer
schemas initialize notebook/job namespace defaults. Existing source-qualified
SSIS schema references remain explicit; selecting bronze/silver/gold does not
rewrite business logic or move all tables into a single schema. The existing
SSIS missing-evidence, static validation and acceptance gates are preserved.

## Interface and implementation

The validation tab now accepts source and target observation JSON files. It
performs exact typed reconciliation and downloads the comparison plus original
captures in a portable ZIP. Synthetic MATCH results remain NOT_VERIFIED for
source equivalence. See [offline evidence](offline-evidence.md) for the schema,
binding requirements, supported types and native acceptance gates.

The previous verification baseline below predates these additions. The current
continuation also passed the eight real .NET 8 compiler integration tests using
official SDK 8.0.426; fixture-stub equivalence does not certify real SSIS runtime
binding or arbitrary scripts.

Verification: **1,080 passed, 12 skipped**, including **48 workbench tests**.
The skips cover eight unavailable .NET checks and four live Databricks provider
checks. The frontend page and health endpoint returned HTTP 200. The installed
wheel reproduced ZIP intake, all four native fixture results, bound workspace
configuration, notebook/job destinations and ZIP hashes outside the checkout.
Actual Databricks execution and source equivalence remain pending user review.

- `ui.py`: upload bounds, path containment, file inventories, explicit runtime
  edits, platform-aware routing, report normalization and binary-safe downloads.
- `workspace.py` and `databricks_workspace.v1.json`: versioned target settings,
  literal notebook widget generation and workspace/cluster job configuration.
- `run_migration(..., workspace_config=...)`: optional backwards-compatible
  setting. Supplied source/output/catalog must agree with the configuration.
- TM1 target contracts bind the workspace configuration before code generation;
  replay reproduces notebook/job defaults. Source review contracts stay blocked.
- SSIS plans and step contracts preserve explicit workspace settings before
  notebook/job assembly; calls without settings keep the previous behavior.

No credentials or source-server connections are required for fixture generation.
TM1 output remains a bounded dense integer leaf-cell preview. Catalogs, schemas,
workspace folders and proposed tables are settings, not automatically created
resources. Source verification, criticality/weights and the mandatory acceptance
checks still apply before migration acceptance.
