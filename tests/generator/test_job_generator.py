from __future__ import annotations

import json

import pytest

from ssis_migration.generator.job_generator import JobDefinition, JobGenerator


# ---------------------------------------------------------
# Fixtures
# ---------------------------------------------------------


@pytest.fixture
def job_generator() -> JobGenerator:
    return JobGenerator()


@pytest.fixture
def simple_plan() -> dict:
    return {
        "package_name": "TestPackage",
        "steps": [
            {
                "step_id": "load_city_dimension",
                "name": "Load City Dimension",
                "step_type": "DIMENSION_LOAD",
                "dependencies": [],
                "metadata": {},
            },
            {
                "step_id": "load_customer_dimension",
                "name": "Load Customer Dimension",
                "step_type": "DIMENSION_LOAD",
                "dependencies": [],
                "metadata": {},
            },
            {
                "step_id": "load_movement_fact",
                "name": "Load Movement Fact",
                "step_type": "FACT_LOAD",
                "dependencies": [],
                "metadata": {},
            },
        ],
        "unresolved": [],
        "metadata": {"planner": "deterministic"},
    }


@pytest.fixture
def plan_with_deps() -> dict:
    return {
        "package_name": "DepPackage",
        "steps": [
            {
                "step_id": "load_dim",
                "name": "Load Dim",
                "step_type": "DIMENSION_LOAD",
                "dependencies": [],
                "metadata": {},
            },
            {
                "step_id": "load_fact",
                "name": "Load Fact",
                "step_type": "FACT_LOAD",
                "dependencies": ["load_dim"],
                "metadata": {},
            },
        ],
        "unresolved": [],
        "metadata": {},
    }


# ---------------------------------------------------------
# Tests
# ---------------------------------------------------------


def test_generate_produces_valid_job_definition(
    job_generator, simple_plan,
):
    job = job_generator.generate(
        simple_plan,
        notebook_path="/Workspace/Test/TestPackage_Migration.ipynb",
    )

    assert isinstance(job, JobDefinition)
    assert job.name == "TestPackage Migration"
    assert len(job.tasks) == 3
    assert job.tags["source"] == "ssis_migration"
    assert job.tags["package"] == "TestPackage"


def test_generate_creates_one_task_per_step(
    job_generator, simple_plan,
):
    job = job_generator.generate(
        simple_plan,
        notebook_path="/Workspace/Test/nb.ipynb",
    )

    task_keys = [t["task_key"] for t in job.tasks]
    assert "load_city_dimension" in task_keys
    assert "load_customer_dimension" in task_keys
    assert "load_movement_fact" in task_keys


def test_generate_implicit_dim_before_fact_deps(
    job_generator, simple_plan,
):
    """Facts with no explicit deps should depend on all dimensions."""
    job = job_generator.generate(
        simple_plan,
        notebook_path="/Workspace/Test/nb.ipynb",
    )

    fact_task = next(
        t for t in job.tasks
        if t["task_key"] == "load_movement_fact"
    )
    assert "depends_on" in fact_task
    dep_keys = [d["task_key"] for d in fact_task["depends_on"]]
    assert "load_city_dimension" in dep_keys
    assert "load_customer_dimension" in dep_keys


def test_generate_respects_explicit_dependencies(
    job_generator, plan_with_deps,
):
    job = job_generator.generate(
        plan_with_deps,
        notebook_path="/Workspace/Test/nb.ipynb",
    )

    fact_task = next(
        t for t in job.tasks
        if t["task_key"] == "load_fact"
    )
    assert "depends_on" in fact_task
    assert fact_task["depends_on"] == [{"task_key": "load_dim"}]


def test_generate_includes_notebook_task_params(
    job_generator, simple_plan,
):
    job = job_generator.generate(
        simple_plan,
        notebook_path="/Workspace/Test/nb.ipynb",
        catalog="my_catalog",
        schema="my_schema",
    )

    task = job.tasks[0]
    nb_task = task["notebook_task"]
    assert nb_task["notebook_path"] == "/Workspace/Test/nb.ipynb"
    assert nb_task["base_parameters"]["step_id"] == "load_city_dimension"
    assert nb_task["base_parameters"]["catalog"] == "my_catalog"
    assert nb_task["base_parameters"]["schema"] == "my_schema"
    assert nb_task["source"] == "WORKSPACE"


def test_generate_includes_job_clusters(
    job_generator, simple_plan,
):
    job = job_generator.generate(
        simple_plan,
        notebook_path="/Workspace/Test/nb.ipynb",
    )

    assert len(job.job_clusters) == 1
    cluster = job.job_clusters[0]
    assert cluster["job_cluster_key"] == "migration_cluster"
    assert "new_cluster" in cluster
    assert "spark_version" in cluster["new_cluster"]


def test_generate_includes_job_parameters(
    job_generator, simple_plan,
):
    job = job_generator.generate(
        simple_plan,
        notebook_path="/Workspace/Test/nb.ipynb",
        catalog="uc_catalog",
    )

    param_names = [p["name"] for p in job.parameters]
    assert "catalog" in param_names
    assert "schema" in param_names
    catalog_param = next(
        p for p in job.parameters if p["name"] == "catalog"
    )
    assert catalog_param["default"] == "uc_catalog"


def test_job_definition_to_dict(
    job_generator, simple_plan,
):
    job = job_generator.generate(
        simple_plan,
        notebook_path="/Workspace/Test/nb.ipynb",
    )
    d = job.to_dict()

    assert d["name"] == "TestPackage Migration"
    assert "tasks" in d
    assert "tags" in d
    assert "job_clusters" in d
    assert "parameters" in d


def test_job_definition_to_json(
    job_generator, simple_plan,
):
    job = job_generator.generate(
        simple_plan,
        notebook_path="/Workspace/Test/nb.ipynb",
    )
    raw = job.to_json()
    parsed = json.loads(raw)

    assert parsed["name"] == "TestPackage Migration"
    assert len(parsed["tasks"]) == 3


def test_generate_dabs_yaml(
    job_generator, simple_plan,
):
    yaml_text = job_generator.generate_dabs_yaml(
        simple_plan,
        notebook_path="/Workspace/Test/nb.ipynb",
        catalog="uc_catalog",
        schema="default",
    )

    assert "bundle:" in yaml_text
    assert "variables:" in yaml_text
    assert "resources:" in yaml_text
    assert "jobs:" in yaml_text
    assert "TestPackage_migration_job:" in yaml_text
    assert "uc_catalog" in yaml_text
    assert "notebook_path:" in yaml_text
    assert "task_key:" in yaml_text


def test_generate_dabs_yaml_includes_dependencies(
    job_generator, plan_with_deps,
):
    yaml_text = job_generator.generate_dabs_yaml(
        plan_with_deps,
        notebook_path="/Workspace/Test/nb.ipynb",
    )

    assert "depends_on:" in yaml_text
    assert "load_dim" in yaml_text


def test_safe_key_handles_special_chars(job_generator):
    assert job_generator._safe_key("Load-City.Dimension") == "load_city_dimension"
    assert job_generator._safe_key("step 1") == "step_1"


def test_generate_with_empty_plan(job_generator):
    plan = {"package_name": "Empty", "steps": [], "unresolved": []}
    job = job_generator.generate(
        plan,
        notebook_path="/Workspace/Test/nb.ipynb",
    )

    assert job.name == "Empty Migration"
    assert len(job.tasks) == 0
