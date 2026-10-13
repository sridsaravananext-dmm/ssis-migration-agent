
from __future__ import annotations

import json
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
SAMPLE_PACKAGE = ROOT / "examples" / "wwi" / "DailyETLMain.dtsx"
SAMPLE_MAPPING = ROOT / "examples" / "wwi" / "connection_mapping.json"
RUN_ROOT = Path(tempfile.gettempdir()) / "ssis_migration_app_runs"

st.set_page_config(
    page_title="SSIS Migration Accelerator",
    page_icon="🔄",
    layout="wide",
)

st.title("SSIS → Databricks Migration Accelerator")
st.caption(
    "Analyze SSIS packages, generate migration artifacts, "
    "and review validation findings."
)

if "migration_result" not in st.session_state:
    st.session_state.migration_result = None

if "migration_output" not in st.session_state:
    st.session_state.migration_output = None

with st.sidebar:
    st.header("Migration configuration")

    catalog = st.text_input(
        "Target catalog",
        value="migration_ai",
        help="Used in generated migration artifacts.",
    )

    ai_enabled = st.checkbox(
        "Enable AI-assisted generation",
        value=False,
        help=(
            "Uses the existing Databricks AI provider for "
            "steps classified as AI-assisted."
        ),
    )

    st.warning(
        "This UI does not deploy resources or execute "
        "generated migration code."
    )

st.subheader("1. Select an SSIS package")

package_mode = st.radio(
    "Package source",
    ["Upload a package", "Use WWI sample"],
    horizontal=True,
)

uploaded_package = None
if package_mode == "Upload a package":
    uploaded_package = st.file_uploader(
        "Upload SSIS package",
        type=["dtsx"],
        help="Choose one .dtsx package.",
    )
else:
    if SAMPLE_PACKAGE.is_file():
        st.success(f"WWI sample found: {SAMPLE_PACKAGE.name}")
    else:
        st.error(
            "WWI sample package was not found in the deployed app. "
            "Upload a package or include examples/wwi in the deployment."
        )

st.subheader("2. Connection mapping")

use_mapping = st.checkbox(
    "Use an existing repository connection_mapping.json",
    value=SAMPLE_MAPPING.is_file(),
    help=(
        "Enables planning and artifact generation when the mapping "
        "and its referenced metadata files are available."
    ),
)

mapping_path = None
if use_mapping:
    default_mapping = (
        str(SAMPLE_MAPPING.relative_to(ROOT))
        if SAMPLE_MAPPING.is_file()
        else ""
    )
    mapping_relative = st.text_input(
        "Mapping file path, relative to repository root",
        value=default_mapping,
    )

    if mapping_relative.strip():
        candidate = (ROOT / mapping_relative).resolve()

        # Restrict the mapping path to the repository.
        if candidate != ROOT and ROOT not in candidate.parents:
            st.error("Mapping path must stay inside the repository.")
        elif candidate.is_file() and candidate.name.endswith(".json"):
            mapping_path = candidate
            st.caption(f"Mapping: {candidate.relative_to(ROOT)}")
        else:
            st.warning("The mapping file does not exist.")

st.subheader("3. Run migration analysis")

st.info(
    "The run begins with parsing, IR construction, and analysis. "
    "When a valid connection mapping is selected, it also runs the "
    "existing planner, artifact generators, and validators."
)

can_run = (
    (package_mode == "Use WWI sample" and SAMPLE_PACKAGE.is_file())
    or (
        package_mode == "Upload a package"
        and uploaded_package is not None
    )
)

if st.button(
    "Run analysis",
    type="primary",
    disabled=not can_run,
):
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = RUN_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        if package_mode == "Use WWI sample":
            source_path = SAMPLE_PACKAGE
        else:
            source_path = run_dir / uploaded_package.name
            source_path.write_bytes(uploaded_package.getvalue())

        from ssis_migration.migrate import run_migration

        output_dir = run_dir / "artifacts"

        with st.spinner(
            "Parsing package and running the migration pipeline..."
        ):
            report = run_migration(
                source=str(source_path),
                output=str(output_dir),
                catalog=catalog.strip() or None,
                connection_mapping=(
                    str(mapping_path) if mapping_path else None
                ),
                base_path=str(ROOT),
                execute=False,
                ai=ai_enabled,
            )

        st.session_state.migration_result = report
        st.session_state.migration_output = str(output_dir)
        st.success("Pipeline finished. Review the reported stage statuses.")

    except Exception as exc:
        st.error(f"Migration pipeline failed: {exc}")
        st.exception(exc)

report = st.session_state.migration_result
output_value = st.session_state.migration_output

if report:
    st.divider()
    st.header("Migration run results")

    packages = report.get("packages", [])
    analyzed = report.get("analyzed_package_count", 0)
    migrated = report.get("migrated_package_count", 0)
    failed = report.get("failed_package_count", 0)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Packages discovered", report.get("package_count", 0))
    c2.metric("Analyzed", analyzed)
    c3.metric("Migration workspaces", migrated)
    c4.metric("Failed", failed)

    st.write("**Overall status:**", report.get("status", "UNKNOWN"))
    st.write("**Deployment:**", report.get("deployment", {}).get("status", "UNKNOWN"))
    st.caption(
        report.get("deployment", {}).get(
            "message", "No deployment status was provided."
        )
    )

    for package in packages:
        with st.expander(
            f"{package.get('name', 'Package')} — {package.get('status', 'UNKNOWN')}",
            expanded=True,
        ):
            st.write("**Source package:**", package.get("package"))
            if package.get("error"):
                st.error(package["error"])

            st.write("**Pipeline stages**")
            st.json(package.get("stages", {}))

            artifacts = package.get("artifacts", {})
            if artifacts:
                st.write("**Artifacts**")
                st.json(artifacts)

                analysis_file = artifacts.get("analysis")
                if analysis_file and Path(analysis_file).is_file():
                    with st.expander("View analysis JSON"):
                        st.json(json.loads(Path(analysis_file).read_text()))

                plan_file = artifacts.get("plan")
                if plan_file and Path(plan_file).is_file():
                    with st.expander("View migration plan"):
                        st.json(json.loads(Path(plan_file).read_text()))

                validation_file = artifacts.get("validation")
                if validation_file and Path(validation_file).is_file():
                    with st.expander("View validation results"):
                        st.json(json.loads(Path(validation_file).read_text()))

    if output_value and Path(output_value).is_dir():
        archive_path = Path(output_value).parent / "migration_artifacts.zip"

        with zipfile.ZipFile(
            archive_path, "w", zipfile.ZIP_DEFLATED
        ) as archive:
            for file_path in Path(output_value).rglob("*"):
                if file_path.is_file():
                    archive.write(
                        file_path,
                        arcname=str(file_path.relative_to(output_value)),
                    )

        st.download_button(
            "Download migration artifacts (.zip)",
            data=archive_path.read_bytes(),
            file_name="migration_artifacts.zip",
            mime="application/zip",
        )

st.divider()
st.caption(
    "Review generated code and validation findings before deployment. "
    "A successful analysis does not mean the migrated workload has been deployed."
)
