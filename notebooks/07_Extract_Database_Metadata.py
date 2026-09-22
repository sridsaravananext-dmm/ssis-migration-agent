from ssis_migration.database.sqlserver.extractor import (
    SQLServerMetadataExtractor,
)


extractor = SQLServerMetadataExtractor(
    server="IN-1N461302K1",
    database="WideWorldImportersDW",
    trusted_connection=True,
)

metadata = extractor.extract()

print("=" * 50)
print("SQL SERVER DATABASE METADATA")
print("=" * 50)

print("Database      :", metadata.database["name"])
print("Schemas       :", len(metadata.schemas))
print("Tables        :", len(metadata.tables))
print("Views         :", len(metadata.views))
print("Procedures    :", len(metadata.procedures))
print("Functions     :", len(metadata.functions))
print("Dependencies  :", len(metadata.dependencies))

print()
print("PROCEDURES")
print("-" * 50)

for procedure in metadata.procedures:
    print(f"{procedure.schema}.{procedure.name}")

print()
print("VIEWS")
print("-" * 50)

for view in metadata.views:
    print(f"{view.schema}.{view.name}")

print()
print("FUNCTIONS")
print("-" * 50)

for function in metadata.functions:
    print(
        f"{function.schema}.{function.name}"
        f" [{function.function_type}]"
    )