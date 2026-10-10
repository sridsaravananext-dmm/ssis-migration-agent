from pathlib import Path

from ssis_migration.parser.package_parser import SSISPackageParser
from ssis_migration.ir.builder import IRBuilder
from ssis_migration.analyzer.package import analyze_package


package_path = Path.cwd() / "examples" / "wwi" / "DailyETLMain.dtsx"

print("=" * 100)
print("PHASE 1 — DETERMINISTIC MIGRATION ENGINE VALIDATION")
print("=" * 100)

print()
print("Package:")
print(package_path)

print()
print("Exists:", package_path.exists())

if not package_path.exists():
    raise FileNotFoundError(
        f"WWI package not found: {package_path}"
    )


# ------------------------------------------------------------------
# 1. Parse
# ------------------------------------------------------------------

parsed = SSISPackageParser(
    str(package_path)
).parse()

print()
print("✓ PARSER SUCCESS")


# ------------------------------------------------------------------
# 2. Build IR
# ------------------------------------------------------------------

package_ir = IRBuilder().build(parsed)

print("✓ IR BUILDER SUCCESS")


# ------------------------------------------------------------------
# 3. Analyze
# ------------------------------------------------------------------

analysis = analyze_package(parsed)

print("✓ ANALYZER SUCCESS")


print()
print("=" * 100)
print("PHASE 1 SUCCESS")
print("=" * 100)

print()
print("Package IR type:", type(package_ir).__name__)
print("Analysis type:", type(analysis).__name__)

print()
print("=" * 100)
print("ANALYSIS STRUCTURE")
print("=" * 100)

if hasattr(analysis, "to_dict"):
    analysis_data = analysis.to_dict()
else:
    analysis_data = analysis

print("Analysis object:", type(analysis).__name__)

if isinstance(analysis_data, dict):
    print()
    print("Top-level keys:")

    for key in analysis_data:
        print("  -", key)
else:
    print("Analysis is not a dictionary.")
    print("Attributes:")

    for name in dir(analysis):
        if not name.startswith("_"):
            print("  -", name)