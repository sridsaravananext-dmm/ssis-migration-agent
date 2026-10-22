# ssis-migration-agent

**Last updated date - 10/22/2026**

Updated MDX analysis: added bounded offline TM1 MDX parsing, source evidence,
missing-dependency reporting and a CLI. Structural parsing does not establish
migration acceptance. See [Phase 8 MDX analysis](docs/phase8-mdx-foundation.md).

Latest validation: **715 tests passed, 4 live integration tests skipped**.
Working branch: `GenericAITool-version01`.

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