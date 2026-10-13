import pytest

from ssis_migration.provisioning import (
    plan_schema_provisioning,
    required_schemas,
)


def test_derives_schemas_from_metadata():
    metadata = {
        "tables": [
            {"schema": "Dimension", "name": "City"},
            {"schema": "Fact", "name": "Sale"},
            {"schema": "dimension", "name": "Customer"},
        ]
    }

    assert required_schemas(metadata) == ["dimension", "fact"]


def test_plans_only_missing_schemas():
    metadata = {
        "tables": [
            {"schema": "Dimension", "name": "City"},
            {"schema": "Fact", "name": "Sale"},
            {"schema": "Integration", "name": "Lineage"},
        ]
    }

    plan = plan_schema_provisioning(
        metadata=metadata,
        catalog="dev_catalog",
        existing_schemas=["default", "dimension"],
    )

    assert plan["required_schemas"] == [
        "dimension", "fact", "integration"
    ]
    assert plan["missing_schemas"] == ["fact", "integration"]
    assert len(plan["statements"]) == 2
    assert plan["execution_enabled"] is False


def test_existing_schemas_need_no_changes():
    metadata = {
        "tables": [
            {"schema": "Dimension", "name": "City"},
        ]
    }

    plan = plan_schema_provisioning(
        metadata,
        "dev_catalog",
        ["dimension"],
    )

    assert plan["missing_schemas"] == []
    assert plan["statements"] == []
    assert plan["status"] == "READY"


def test_invalid_metadata_is_rejected():
    with pytest.raises(ValueError):
        required_schemas({"tables": "not-a-list"})
