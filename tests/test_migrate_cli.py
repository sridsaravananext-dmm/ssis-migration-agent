from __future__ import annotations

import json
from pathlib import Path

import pytest

from ssis_migration.migrate import run_migration


# ---------------------------------------------------------
# Path fixtures
# ---------------------------------------------------------


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DTSX_PATH = PROJECT_ROOT / "examples" / "wwi" / "DailyETLMain.dtsx"
MAPPING_PATH = (
    PROJECT_ROOT / "examples" / "wwi" / "connection_mapping.json"
)


@pytest.fixture
def migration_result(tmp_path: Path) -> dict:
    """Run the full migration pipeline on DailyETLMain.dtsx."""
    return run_migration(
        source=str(DTSX_PATH),
        output=str(tmp_path),
        catalog=None,
        connection_mapping=str(MAPPING_PATH),
        base_path=str(PROJECT_ROOT),
        execute=False,
    )


@pytest.fixture
def package_output(
    migration_result: dict, tmp_path: Path
) -> Path:
    """Return the per-package output directory."""
    pkg = migration_result["packages"][0]
    return Path(pkg["artifacts"]["manifest"]).parent


# ---------------------------------------------------------
# Tests
# ---------------------------------------------------------


def test_migration_report_status(migration_result: dict):
    """The full pipeline should complete with MIGRATION_COMPLETE."""
    assert migration_result["status"] == (
        "MIGRATION_COMPLETE_DEPLOYMENT_PENDING"
    )
    assert migration_result["migrated_package_count"] == 1
    assert migration_result["failed_package_count"] == 0


def test_package_status_is_migrated(migration_result: dict):
    """The package should be marked MIGRATED, not just ANALYZED."""
    pkg = migration_result["packages"][0]
    assert pkg["status"] == "MIGRATED"


def test_all_stages_succeeded(migration_result: dict):
    """All connected stages should report SUCCEEDED."""
    pkg = migration_result["packages"][0]
    stages = pkg["stages"]
    assert stages["package_discovery"] == "SUCCEEDED"
    assert stages["parsing"] == "SUCCEEDED"
    assert stages["ir_build"] == "SUCCEEDED"
    assert stages["analysis"] == "SUCCEEDED"
    assert stages["target_metadata_discovery"] == "SUCCEEDED"
    assert stages["transformation_generation"] == "SUCCEEDED"
    assert stages["data_validation"] == "SUCCEEDED"


def test_all_expected_files_exist(package_output: Path):
    """All expected output files should be present."""
    expected_files = [
        "package_ir.json",
        "analysis.json",
        "migration_manifest.json",
        "migration_plan.json",
        "validation_results.json",
        "DailyETLMain_Migration.ipynb",
    ]
    for filename in expected_files:
        filepath = package_output / filename
        assert filepath.exists(), (
            f"Missing expected file: {filename}"
        )


def test_generated_artifacts_directory_has_files(
    package_output: Path,
):
    """The generated_artifacts directory should contain files."""
    artifacts_dir = package_output / "generated_artifacts"
    assert artifacts_dir.exists()
    assert artifacts_dir.is_dir()
    files = list(artifacts_dir.iterdir())
    assert len(files) > 0


def test_notebook_is_valid_ipynb(package_output: Path):
    """The notebook should be valid Jupyter .ipynb format."""
    notebook_path = (
        package_output / "DailyETLMain_Migration.ipynb"
    )
    content = notebook_path.read_text(encoding="utf-8")
    nb = json.loads(content)

    assert nb["nbformat"] == 4
    assert "cells" in nb
    assert len(nb["cells"]) > 0

    # Should have both markdown and code cells
    cell_types = {c["cell_type"] for c in nb["cells"]}
    assert "markdown" in cell_types
    assert "code" in cell_types


def test_migration_plan_has_steps(package_output: Path):
    """The migration plan should contain migration steps."""
    plan_path = package_output / "migration_plan.json"
    plan = json.loads(plan_path.read_text())

    assert plan["package_name"] == "DailyETLMain"
    assert len(plan["steps"]) > 0
    assert plan["unresolved"] == []


def test_validation_results_exist(package_output: Path):
    """Validation results should be present and non-empty."""
    val_path = package_output / "validation_results.json"
    validations = json.loads(val_path.read_text())

    assert len(validations) > 0
    # All validations should be PASS or REVIEW_REQUIRED
    for v in validations:
        assert v["status"] in (
            "PASS",
            "REVIEW_REQUIRED",
        ), (
            f"Unexpected status {v['status']} "
            f"for {v['step_name']}"
        )


def test_manifest_includes_notebook_path(
    package_output: Path,
):
    """The manifest should include the notebook artifact path."""
    manifest_path = (
        package_output / "migration_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text())

    assert "notebook" in manifest["artifacts"]
    notebook_path = Path(manifest["artifacts"]["notebook"])
    assert notebook_path.exists()
    assert notebook_path.suffix == ".ipynb"