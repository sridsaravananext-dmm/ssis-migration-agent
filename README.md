# ssis-migration-agent

**Last updated date - 10/22/2026**

The [developer workbench](docs/developer-workbench.md) now connects TM1 bundle
uploads and source paths to Databricks candidate generation. Configure catalog,
bronze/silver/gold schemas, runtime table bindings and parameters, notebook
destination, cluster and artifact paths in the browser; download notebooks and
validation artifacts for your Databricks review. Existing SSIS upload and
dependency-role controls remain available.

TM1 language analysis now includes bounded offline [MDX](docs/phase8-mdx-foundation.md),
[rules/feeders](docs/phase8-tm1-rules-feeders.md) and
[TurboIntegrator sections/control flow](docs/phase8-turbointegrator-analysis.md).
Reports preserve source evidence and unresolved dependencies. Structural parsing
does not establish migration acceptance. Databricks is the current migration target.

Supplied [dimensional metadata and process binding](docs/phase8-tm1-metadata-binding.md)
now supports evidence-backed cube/member, parameter and datasource declarations.
Dynamic values and missing/ambiguous definitions remain unresolved; metadata
matching does not establish runtime equivalence.

The [TM1 bundle adapter](docs/phase8-tm1-native-integration.md) now builds native
IR v2 and review-only migration contracts through the shared API/CLI. The SSIS
v1 pipeline remains supported. An explicit target mapping now enables
[bounded TM1 Databricks candidates](docs/tm1-databricks-candidate.md): numeric TI
row snapshots, literal MDX leaf queries and constant leaf rules. Unsupported
behavior blocks executable output. Native `.pro` decoding is not implemented.

Generation produces self-contained preview/reconciliation notebooks, job/DAB
templates, source-bound contracts, reports and a review ZIP. It stops for your
Databricks review: no deployment, persistent writes or acceptance. Semantic
coverage and source equivalence remain unverified. This checkpoint stops at
generation pending your Databricks review.
Working branch: `GenericAITool-version01`.

Validation: **1,070 regression tests passed, 12 skipped**, including **38 workbench
tests** and **72 candidate tests**. Installed-wheel generation, source/contract
replay, ZIP integrity, SSIS compatibility and local frontend HTTP checks passed.
Databricks execution and source equivalence await your review.

# ============================================================
# README.md
# ============================================================

readme = """
# SSIS Migration Agent

Reusable AI-driven platform for analyzing and migrating SSIS
packages to modern data platforms.

## Architecture

SSIS DTSX
    |
    v
Universal SSIS Parser
    |
    v
Generic Parsed Model
    |
    v
Universal Intermediate Representation
    |
    v
Deterministic Analyzer
    |
    v
AI Migration Agent
    |
    v
Migration Blueprint
    |
    v
Target Code Generator
    |
    +--> Databricks
    +--> Microsoft Fabric
    +--> Snowflake
    +--> Future targets
    |
    v
Validator

## Repository Structure

- `src/` - reusable Python implementation
- `schemas/` - canonical JSON schemas
- `notebooks/` - Databricks orchestration
- `tests/` - automated tests
- `examples/` - example migration projects

## Design Principle

The parser must remain generic and must not contain
customer-specific or project-specific migration logic.

WWI is used only as an example/test fixture.
"""


path = REPO_ROOT / "README.md"

path.write_text(
    readme.strip() + "\n",
    encoding="utf-8"
)

print(f"✓ Created {path}")
