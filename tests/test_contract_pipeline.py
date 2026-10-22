import json
from pathlib import Path

from ssis_migration.migrate import run_migration
from ssis_migration.migration import validate_contract


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/wwi/DailyETLMain.dtsx"


def test_all_contracts_are_saved_before_first_target_generation(tmp_path, monkeypatch):
    from ssis_migration.generator.dispatcher import GeneratorDispatcher

    original = GeneratorDispatcher.generate
    seen = []

    def generate(self, step, *args, **kwargs):
        bundle_file = tmp_path / "DailyETLMain/migration_contracts.json"
        assert bundle_file.is_file()
        bundle = json.loads(bundle_file.read_text())
        assert len(bundle["contracts"]) == 13
        saved = next(contract for contract in bundle["contracts"] if contract["step_id"] == step["step_id"])
        assert saved == step["metadata"]["migration_contract"]
        validate_contract(saved, step)
        seen.append(saved["contract_id"])
        return original(self, step, *args, **kwargs)

    monkeypatch.setattr(GeneratorDispatcher, "generate", generate)
    report = run_migration(str(SOURCE), str(tmp_path), base_path=str(ROOT))
    assert report["migrated_package_count"] == 1
    package = report["packages"][0]
    assert package["stages"]["migration_contract_generation"] == "SUCCEEDED"
    bundle = json.loads(Path(package["artifacts"]["contracts"]).read_text())
    assert set(seen) == {contract["contract_id"] for contract in bundle["contracts"]}
    assert len(set(seen)) == 13
    for contract in bundle["contracts"]:
        assert contract["source_behavior"]["diagnostics"]
        assert contract["unresolved_items"]  # Source gaps cannot vanish at the bridge.
        assert contract["required_behavior"]["coverage_status"] == "NOT_VERIFIED"
    assert package["acceptance"]["accepted"] is False
    manifest = json.loads(Path(package["artifacts"]["manifest"]).read_text())
    assert manifest["artifacts"]["contracts"] == package["artifacts"]["contracts"]


def test_invalid_contract_fails_package_before_code_generation(tmp_path, monkeypatch):
    from ssis_migration import migration
    from ssis_migration.generator.dispatcher import GeneratorDispatcher

    original = migration.build_migration_contract

    def invalid(*args, **kwargs):
        contract = original(*args, **kwargs)
        contract.contract_version = "unsupported"
        return contract

    def forbidden(*args, **kwargs):
        raise AssertionError("Target generation occurred before contract validation")

    monkeypatch.setattr(migration, "build_migration_contract", invalid)
    monkeypatch.setattr(GeneratorDispatcher, "generate", forbidden)
    report = run_migration(str(SOURCE), str(tmp_path), base_path=str(ROOT))
    assert report["failed_package_count"] == 1
    assert "contract_version" in report["packages"][0]["error"]
    assert "notebook" not in report["packages"][0]["artifacts"]
    assert not list((tmp_path / "DailyETLMain/generated_artifacts").iterdir())
