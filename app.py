from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Paths - app.py lives in the repo root
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
EXAMPLES = ROOT / "examples"
SAMPLE_PACKAGE = EXAMPLES / "wwi" / "DailyETLMain.dtsx"
SAMPLE_MAPPING = EXAMPLES / "wwi" / "connection_mapping.json"
RUN_ROOT = Path(tempfile.gettempdir()) / "ssis_migration_app_runs"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="SSIS Migration Accelerator",
    page_icon="🔄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
for _key, _default in {
    "migration_result": None,
    "migration_output": None,
    "ddl_text": None,
    "ddl_report": None,
}.items():
    if _key not in st.session_state:
        st.session_state[_key] = _default

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Configuration")
    catalog = st.text_input("Target catalog", value="dev_catalog")
    ai_enabled = st.checkbox("Enable AI-assisted generation", value=False)
    platform_labels = {
        "Detect from supplied files": None,
        "SSIS": "ssis",
        "TM1 / Planning Analytics": "tm1",
        "Informatica PowerCenter": "informatica",
        "SAP Data Services (BODS)": "bods",
        "Oracle Data Integrator (ODI)": "odi",
    }
    platform_choice = st.selectbox("Confirm source platform", options=list(platform_labels))
    source_platform = platform_labels[platform_choice]
    st.caption("Only SSIS currently generates Databricks artifacts. Other source files are inventoried and their unsupported migration is reported explicitly.")
    st.divider()
    st.markdown("#### About")
    st.caption("SSIS -> Databricks Migration Accelerator.")
    st.warning("This UI does not execute code or deploy without explicit action.", icon="⚠️")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _file_language(name):
    ext = Path(name).suffix.lower()
    return {".py": "python", ".sql": "sql", ".json": "json", ".yaml": "yaml",
            ".yml": "yaml", ".ipynb": "json", ".md": "markdown", ".csv": "csv"}.get(ext, "text")

def _format_size(size):
    for unit in ["B", "KB", "MB"]:
        if size < 1024: return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"

def _artifact_tree(base):
    items = []
    for path in sorted(base.iterdir()):
        rel = str(path.relative_to(base))
        if path.is_dir():
            items.append({"path": rel, "is_dir": True, "size": 0})
            items.extend(_artifact_tree(path))
        else:
            items.append({"path": rel, "is_dir": False, "size": path.stat().st_size})
    return items

def _run_migration(source_path, output_dir, mapping_path, procedures_path=None, ddl_path=None,
                   *, dependency_files=(), file_roles=None, source_platform=None,
                   dependency_base=None):
    from ssis_migration.migrate import run_migration
    return run_migration(
        source=str(source_path), output=str(output_dir),
        catalog=catalog.strip() or None,
        connection_mapping=str(mapping_path) if mapping_path else None,
        base_path=str(dependency_base or ROOT), execute=False, ai=ai_enabled,
        ai_provider="databricks",
        ddl_path=str(ddl_path) if ddl_path else None,
        procedures_path=str(procedures_path) if procedures_path else None,
        dependency_files=[str(path) for path in dependency_files],
        file_roles=file_roles or {},
        source_platform=source_platform,
    )

def _save_dependencies(uploaded_files):
    """Preserve every uploaded file, including duplicate basenames."""
    files = [(Path(item.name).name or "uploaded_file", item.getvalue()) for item in uploaded_files]
    digest = hashlib.sha256()
    for name, content in files:
        digest.update(name.encode("utf-8"))
        digest.update(hashlib.sha256(content).digest())
    signature = digest.hexdigest()
    if st.session_state.get("dependency_upload_signature") != signature:
        uploads_root = RUN_ROOT / "uploads"
        uploads_root.mkdir(parents=True, exist_ok=True)
        st.session_state.dependency_upload_directory = tempfile.mkdtemp(prefix="dependencies-", dir=str(uploads_root))
        st.session_state.dependency_upload_signature = signature
    directory = Path(st.session_state.dependency_upload_directory)
    counts = {name: sum(other_name == name for other_name, _ in files) for name, _ in files}
    saved = []
    for index, (name, content) in enumerate(files):
        # Duplicate names have no arbitrary winner for mapping resolution.
        parent = directory / f"duplicate-{index}" if counts[name] > 1 else directory
        parent.mkdir(parents=True, exist_ok=True)
        path = parent / name
        if not path.exists():
            path.write_bytes(content)
        saved.append((name, path))
    return directory, saved

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_upload, tab_run, tab_artifacts, tab_ddl = st.tabs([
    "📦 Upload & Configure", "🚀 Run Migration", "📂 Artifacts", "📋 DDL Preview"])

# === TAB 1: Upload & Configure ===
with tab_upload:
    st.header("1. Select Exported Source")
    package_mode = st.radio("Package source", ["Upload a source package or script", "Use WWI sample package"], horizontal=True)
    uploaded_package = None
    if package_mode == "Upload a source package or script":
        uploaded_package = st.file_uploader("Upload exported source package or script")
        st.caption("Files are identified by content. A filename or platform choice does not establish parser support.")
    else:
        if SAMPLE_PACKAGE.is_file():
            st.success(f"WWI sample found: `{SAMPLE_PACKAGE.name}`")
        else:
            st.error("WWI sample not found. Upload a package instead.")

    st.divider()
    st.header("2. Upload Dependencies (optional)")
    st.caption("Supply schema metadata, procedure definitions, connection mappings, parameters and other dependencies as files. Review detected roles or confirm them below. Every supplied file remains in the inventory.")
    uploaded_deps = st.file_uploader(
        "Upload dependency files",
        accept_multiple_files=True,
        key="deps_upload",
    )

    mapping_path = None
    procedures_path = None
    ddl_path = None
    dependency_files = []
    file_roles = {}
    dependency_base = None

    if uploaded_deps:
        from ssis_migration.intake import describe_file

        upload_dir, saved_dependencies = _save_dependencies(uploaded_deps)
        dependency_files = [path for _, path in saved_dependencies]
        role_choices = {
            "Detect automatically": None,
            "Connection mapping": "connection_mapping",
            "Database metadata": "database_metadata",
            "Procedure definitions": "procedures",
            "DDL schema": "ddl",
            "Parameters": "parameters",
            "Configuration": "configuration",
            "Script": "script",
            "Source package": "package",
        }
        selected_paths = {role: [] for role in ("connection_mapping", "procedures", "ddl")}
        duplicate_names = {name for name, _ in saved_dependencies if sum(other == name for other, _ in saved_dependencies) > 1}
        for name in sorted(duplicate_names):
            st.warning(f"Multiple files are named `{name}`. All are retained separately; references to that basename need disambiguation.")

        st.write("**Detected dependencies:**")
        for name, saved in saved_dependencies:
            record = describe_file(saved, source_root=upload_dir)
            detected_roles = record.get("detected_roles", [])
            st.write(f"- `{name}` → {', '.join(detected_roles) or 'unknown'}")
            chosen = st.selectbox(
                f"Confirm role for {saved.relative_to(upload_dir)}",
                options=list(role_choices),
                key=f"dependency_role_{record['file_id']}",
            )
            confirmed_role = role_choices[chosen]
            if confirmed_role is not None:
                file_roles[str(saved)] = confirmed_role
            effective_role = confirmed_role or record.get("role", "unknown")
            if effective_role in selected_paths:
                selected_paths[effective_role].append(saved)
            elif effective_role in {"ambiguous", "unknown"}:
                st.warning(f"`{name}` needs a role decision and remains in the inventory.")

        for role, paths in selected_paths.items():
            if len(paths) > 1:
                st.warning(f"Multiple files have role `{role}`. None is selected arbitrarily; resolve the intake findings before generation.")
        mapping_path = selected_paths["connection_mapping"][0] if len(selected_paths["connection_mapping"]) == 1 else None
        procedures_path = selected_paths["procedures"][0] if len(selected_paths["procedures"]) == 1 else None
        ddl_path = selected_paths["ddl"][0] if len(selected_paths["ddl"]) == 1 else None
        if mapping_path is not None:
            dependency_base = upload_dir
        if ddl_path is not None:
            st.session_state.ddl_text = ddl_path.read_text(encoding="utf-8", errors="replace")

    st.divider()
    st.header("3. Summary")
    has_package = (package_mode == "Use WWI sample package" and SAMPLE_PACKAGE.is_file()) or (uploaded_package is not None)
    can_run = has_package
    c1, c2, c3 = st.columns(3)
    c1.metric("Package", "✅" if has_package else "❌")
    c2.metric("Procedures", "✅" if procedures_path else "—")
    c3.metric("DDL", "✅" if ddl_path else "—")

# === TAB 2: Run Migration ===
with tab_run:
    st.header("Run Migration Pipeline")
    if not can_run:
        st.info("Complete configuration in the **Upload & Configure** tab first.")
    else:
        st.markdown("Pipeline: Parse → IR → Analyze → Plan → Generate → Notebook")
        if st.button("🚀 Run Migration", type="primary"):
            run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            run_dir = RUN_ROOT / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            try:
                source_path = SAMPLE_PACKAGE if package_mode == "Use WWI sample package" else run_dir / Path(uploaded_package.name).name
                if uploaded_package:
                    source_path.write_bytes(uploaded_package.getvalue())
                output_dir = run_dir / "artifacts"
                progress = st.progress(0, text="Starting...")
                with st.spinner("Running migration pipeline..."):
                    progress.progress(20, text="Parsing & analyzing...")
                    report = _run_migration(
                        source_path, output_dir, mapping_path, procedures_path, ddl_path,
                        dependency_files=dependency_files, file_roles=file_roles,
                        source_platform=source_platform, dependency_base=dependency_base,
                    )
                    progress.progress(80, text="Generating notebook...")
                    st.session_state.migration_result = report
                    st.session_state.migration_output = str(output_dir)
                    progress.progress(100, text="Done!")
                if report.get("lifecycle_status") == "BLOCKED":
                    st.warning("Artifacts generated with blocking findings. Review the validation results before running them.")
                else:
                    st.success("Artifact generation completed. Execution and data equivalence have not been verified.")
            except Exception as exc:
                st.error(f"Pipeline failed: {exc}")
                with st.expander("Traceback"):
                    st.exception(exc)

    report = st.session_state.migration_result
    output_value = st.session_state.migration_output
    if report:
        st.divider()
        st.header("Migration Results")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Discovered", report.get("package_count", 0))
        c2.metric("Analyzed", report.get("analyzed_package_count", 0))
        c3.metric("Generated", report.get("migrated_package_count", 0))
        c4.metric("Failed", report.get("failed_package_count", 0))
        st.write(f"**Status:** `{report.get('lifecycle_status', 'UNKNOWN')}`")
        st.caption("Static checks describe the generated files. Execution and data equivalence have not been verified.")
        intake = report.get("source_intake", {})
        intake_path = report.get("intake_manifest")
        if intake_path and Path(intake_path).is_file():
            intake = json.loads(Path(intake_path).read_text(encoding="utf-8"))
        if intake:
            st.subheader("Source Intake")
            st.write(f"**Detected platform:** `{intake.get('detected_platform', 'unknown')}` · **Confirmed platform:** `{intake.get('confirmed_platform') or 'auto'}` · **Intake status:** `{intake.get('status', 'UNKNOWN')}`")
            if intake_path:
                st.caption(f"Inventory: {Path(intake_path).name}")
            diagnostics = intake.get("diagnostics", [])
            if diagnostics:
                with st.expander(f"Intake findings ({len(diagnostics)})"):
                    for diagnostic in diagnostics:
                        st.write(f"`{diagnostic.get('severity', 'INFO')}` `{diagnostic.get('code', 'INTAKE')}`: {diagnostic.get('message', '')}")
        for package in report.get("packages", []):
            with st.expander(f"📦 {package.get('name', '?')} — {package.get('lifecycle_status', 'UNKNOWN')}", expanded=True):
                if package.get("error"):
                    st.error(package["error"])
                stages = package.get("stages", {})
                for sn, ss in stages.items():
                    emoji = "✅" if ss == "SUCCEEDED" else "⚠️" if "NOT" in str(ss) else "❌"
                    st.write(f"{emoji} `{sn}`: {ss}")
                artifacts = package.get("artifacts", {})
                if artifacts:
                    st.write("**Artifacts:**")
                    for name, path_str in artifacts.items():
                        if isinstance(path_str, str):
                            st.write(f"{'✅' if Path(path_str).exists() else '❌'} `{name}`")
                        else:
                            st.write(f"`{name}`: {path_str}")
                # Show the review report inline (not nested in expander)
                review_fp = artifacts.get("review")
                if review_fp and Path(review_fp).is_file():
                    st.markdown("---")
                    st.markdown(Path(review_fp).read_text())
        if output_value and Path(output_value).is_dir():
            archive = Path(output_value).parent / "migration_artifacts.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as a:
                for fp in Path(output_value).rglob("*"):
                    if fp.is_file(): a.write(fp, arcname=str(fp.relative_to(output_value)))
            st.download_button("📥 Download artifacts (.zip)", data=archive.read_bytes(), file_name="migration_artifacts.zip")

# === TAB 3: Artifacts Browser ===
with tab_artifacts:
    st.header("Artifact Browser")
    output_value = st.session_state.migration_output
    if not output_value or not Path(output_value).is_dir():
        st.info("Run a migration first to browse artifacts.")
    else:
        output_path = Path(output_value)
        pkg_dirs = [d for d in output_path.iterdir() if d.is_dir()]
        if not pkg_dirs:
            st.warning("No packages found.")
        else:
            selected_pkg = st.selectbox("Select package", options=[d.name for d in pkg_dirs])
            files = _artifact_tree(output_path / selected_pkg)
            if files:
                file_choices = {f["path"]: f for f in files if not f["is_dir"]}
                col1, col2 = st.columns([1, 2])
                with col1:
                    st.write("**Files**")
                    selected_file = st.selectbox("File", options=list(file_choices.keys()))
                with col2:
                    if selected_file:
                        file_full = output_path / selected_pkg / selected_file
                        st.write(f"**{selected_file}** ({_format_size(file_choices[selected_file]['size'])})")
                        content = file_full.read_text(encoding="utf-8", errors="replace")
                        if selected_file.endswith(".ipynb"):
                            try:
                                nb = json.loads(content)
                                st.write(f"Notebook: {len(nb.get('cells', []))} cells")
                                with st.expander("View JSON"): st.code(content[:10000], language="json")
                            except: st.code(content[:10000], language=_file_language(selected_file))
                        else:
                            st.code(content[:15000], language=_file_language(selected_file))
                        st.download_button(f"📥 Download", data=content.encode(), file_name=Path(selected_file).name)

# === TAB 4: DDL Preview ===
with tab_ddl:
    st.header("DDL Preview")
    ddl_text = st.session_state.ddl_text
    if not ddl_text:
        st.info("Upload a DDL file in the **Upload & Configure** tab to preview it here.")
    else:
        st.markdown("Preview of the uploaded DDL file.")
        create_count = len(re.findall(r"CREATE\s+TABLE", ddl_text, re.IGNORECASE))
        st.caption(f"{create_count} CREATE TABLE statements")
        st.code(ddl_text, language="sql")
        st.download_button("📥 Download DDL", data=ddl_text.encode(), file_name="ddl.sql")

st.divider()
st.caption("SSIS -> Databricks Migration Accelerator | Review artifacts before deployment.")
