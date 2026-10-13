# Key Files and Responsibilities

This index records responsibilities supported by inspected code or current
repository inventory. Where the implementation has not been inspected fully,
the description is intentionally limited.

## Entry points and pipeline

| File | Responsibility |
|---|---|
| `src/ssis_migration/migrate.py` | Package analysis pipeline and migration reports. The observed report mode is analysis-only. |
| `src/ssis_migration/migration_runner.py` | Structure-migration runner, including dry-run planning and an execution option. |
| `src/ssis_migration/provisioning.py` | Structure provisioning module; inspect implementation before relying on execution behavior. |
| `notebooks/01_Parse_DTSX.py` | Notebook-stage artifact for parsing a DTSX package. |
| `notebooks/02_Build_IR.py` | Notebook-stage artifact for building package IR. |
| `notebooks/03_Analyze.py` | Notebook-stage artifact for package analysis. |
| `notebooks/04_Generate_Blueprint.py` | Notebook-stage artifact for blueprint generation. |
| `notebooks/05_Generate_Code.py` | Notebook-stage artifact for code generation. |
| `notebooks/06_Validate.py` | Notebook-stage artifact for validation. |
| `notebooks/07_Extract_Database_Metadata.py` | Notebook-stage artifact for metadata extraction. |

## Parsing, IR, and analysis

| File | Responsibility |
|---|---|
| `src/ssis_migration/parser/package_parser.py` | Parses SSIS package XML into the parsed package representation. |
| `src/ssis_migration/ir/builder.py` | Builds package IR from parsed package data. |
| `src/ssis_migration/analyzer/__init__.py` | Exposes package/data-flow/component analysis functions. |
| `src/ssis_migration/analyzer/deterministic_resolver.py` | Resolves a migration-contract dictionary into a strategy, generation status, reasons, unresolved items, and required capabilities. |
| `src/ssis_migration/planner/migration_strategy.py` | Determines a strategy from source-procedure patterns and complexity signals. |

## Database metadata and context

| File | Responsibility |
|---|---|
| `src/ssis_migration/database/connection_mapping.py` | Defines `ConnectionDatabaseMapping` and `ConnectionMappingSet`. |
| `src/ssis_migration/database/connection_mapping_loader.py` | Reads the mapping JSON and constructs the mapping objects. |
| `src/ssis_migration/context/builder.py` | Builds migration context by loading mapped metadata snapshots, building database IR, and selecting relevant objects and dependency edges. |
| `src/ssis_migration/context/migration_context.py` | Defines serializable database and migration context data structures. |
| `examples/wwi/connection_mapping.json` | Maps source/destination SSIS connection references to metadata files, database names, and roles. |
| `examples/wwi/database_metadata_wwi.json` | Exported metadata snapshot for the source database. |
| `examples/wwi/database_metadata_wwi_dw.json` | Exported metadata snapshot for the destination database. |

## AI agent and generation

| File | Responsibility |
|---|---|
| `src/ssis_migration/agent/migration_contract.py` | Defines the migration contract and semantic-risk data structures. |
| `src/ssis_migration/agent/orchestrator.py` | Coordinates context building, planning, prompt construction, provider invocation, response validation, and static code validation. |
| `src/ssis_migration/agent/planner.py` | AI migration request/planning component; inspect full implementation before documenting details. |
| `src/ssis_migration/agent/provider_factory.py` | Provider construction/selection; inspect implementation and tests for exact behavior. |
| `src/ssis_migration/generator/dispatcher.py` | Selects a target-code generator using migration metadata and strategy/language decisions. |
| `src/ssis_migration/generator/spark_sql.py` | Spark SQL generator. |
| `src/ssis_migration/generator/pyspark.py` | PySpark generator. |
| `src/ssis_migration/validator/generated_code.py` | Generated-code validation module; exact checks require further inspection. |
| `.assistant/skills/migration-intelligence/SKILL.md` | Platform-independent migration reasoning instructions and behavior-preservation rules. |

## Sample artifacts

| File | Responsibility |
|---|---|
| `examples/wwi/DailyETLMain.dtsx` | SSIS package used by the current WideWorldImporters sample. |
| `examples/wwi/parsed.json` | Saved parsed-package output. |
| `examples/wwi/package_ir.json` | Saved package intermediate representation. |
| `examples/wwi/analysis.json` | Saved analysis output. |
| `examples/wwi/migration_blueprint.json` | Structured migration blueprint. |
| `examples/wwi/generated_dw_ddl_preview.sql` | Generated DDL preview. |
| `examples/wwi/generated_dw_ddl_review.json` | DDL review report; currently reports review required. |
| `examples/wwi/migration_run_report.json` | Structure-run report; currently a dry run with no table creation. |
| `generated_migrations/migration_report.json` | Top-level analysis-only migration report. |

## Tests to consult

| File | Test focus inferred from filename |
|---|---|
| `tests/planner/test_migration_strategy.py` | Migration-strategy behavior. |
| `tests/resolver/test_registry.py` | Resolver registry behavior. |
| `tests/resolver/test_sql_features.py` | SQL-feature resolution behavior. |
| `tests/agent/test_openai_provider.py` | OpenAI provider behavior. |
| `tests/test_ddl_generator.py` | DDL generation. |
| `tests/test_provisioning.py` | Provisioning behavior. |
| `tests/validator/test_sql_semantic.py` | SQL semantic validation. |

Test focus above is inferred from filenames; inspect test bodies before treating it as a complete description of coverage.
