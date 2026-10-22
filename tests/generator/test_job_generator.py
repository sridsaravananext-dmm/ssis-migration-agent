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


def test_generate_does_not_invent_dim_before_fact_deps(
    job_generator, simple_plan,
):
    """Task types alone are not evidence of a source dependency."""
    job = job_generator.generate(
        simple_plan,
        notebook_path="/Workspace/Test/nb.ipynb",
    )

    fact_task = next(
        t for t in job.tasks
        if t["task_key"] == "load_movement_fact"
    )
    assert "depends_on" not in fact_task


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

    # JSON is a YAML-compatible representation with no optional parser.
    bundle = json.loads(yaml_text)
    assert bundle["bundle"]["name"] == "TestPackage_migration"
    assert bundle["variables"]["catalog"]["default"] == "uc_catalog"
    job = bundle["resources"]["jobs"]["TestPackage_migration_job"]
    assert len(job["tasks"]) == len(simple_plan["steps"])
    assert job["tasks"][0]["notebook_task"]["notebook_path"] == "/Workspace/Test/nb.ipynb"
    assert job["tasks"][0]["task_key"] == "load_city_dimension"


def test_generate_dabs_yaml_includes_dependencies(
    job_generator, plan_with_deps,
):
    yaml_text = job_generator.generate_dabs_yaml(
        plan_with_deps,
        notebook_path="/Workspace/Test/nb.ipynb",
    )

    job = json.loads(yaml_text)["resources"]["jobs"]["DepPackage_migration_job"]
    assert job["tasks"][1]["depends_on"] == [{"task_key": "load_dim"}]


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


def test_generate_preserves_forward_dependencies_and_input_order(job_generator):
    plan = {
        "package_name": "Forward",
        "steps": [
            {"step_id": "a", "dependencies": ["z"]},
            {"step_id": "z", "dependencies": []},
        ],
    }
    job = job_generator.generate(plan, "/Workspace/Forward.ipynb")
    assert [task["task_key"] for task in job.tasks] == ["a", "z"]
    assert job.tasks[0]["depends_on"] == [{"task_key": "z"}]


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ([{"step_id": "same"}, {"step_id": "same"}], "Duplicate"),
        ([{"step_id": "A-B"}, {"step_id": "a.b"}], "collision"),
        ([{"name": "missing id"}], "nonempty step_id"),
        ([{"step_id": ""}], "nonempty step_id"),
        ([{"step_id": "!!!"}], "Invalid Databricks task key"),
        ([{"step_id": "a", "dependencies": ["missing"]}], "Unknown dependencies"),
        ([{"step_id": "a", "dependencies": ["a"]}], "Circular"),
        ([{"step_id": "a", "dependencies": "a"}], "list of step IDs"),
        ([{"step_id": "a", "dependencies": ["b"]},
          {"step_id": "b", "dependencies": ["a"]}], "Circular"),
    ],
)
def test_generate_rejects_invalid_dependency_graphs(job_generator, steps, message):
    with pytest.raises(ValueError, match=message):
        job_generator.generate({"steps": steps}, "/Workspace/Test.ipynb")


def test_dabs_compute_references_parameters_and_escaping(job_generator):
    name = 'Quoted "package":\nnext'
    path = '/Workspace/"quoted"\\path\nnext.ipynb'
    plan = {
        "package_name": name,
        "steps": [{"step_id": "first", "name": 'quoted "step":\nnext'}],
    }
    bundle = json.loads(job_generator.generate_dabs_yaml(
        plan, path, catalog='catalog"name', schema="schema: name",
    ))
    assert bundle["variables"]["catalog"]["default"] == 'catalog"name'
    assert bundle["variables"]["schema"]["default"] == "schema: name"
    job = next(iter(bundle["resources"]["jobs"].values()))
    assert job["name"] == f"{name} Migration"
    assert job["tags"]["package"] == name
    clusters = {cluster["job_cluster_key"] for cluster in job["job_clusters"]}
    assert clusters == {"migration_cluster"}
    for task in job["tasks"]:
        assert task["job_cluster_key"] in clusters
        assert task["notebook_task"]["notebook_path"] == path
        assert task["notebook_task"]["base_parameters"] == {
            "step_id": "first", "catalog": "${var.catalog}", "schema": "${var.schema}",
        }
    assert job["parameters"] == [
        {"name": "catalog", "default": "${var.catalog}"},
        {"name": "schema", "default": "${var.schema}"},
    ]


def test_dabs_rejects_invalid_graph_before_serialization(job_generator):
    with pytest.raises(ValueError, match="Unknown dependencies"):
        job_generator.generate_dabs_yaml(
            {"steps": [{"step_id": "a", "dependencies": ["missing"]}]},
            "/Workspace/Test.ipynb",
        )
