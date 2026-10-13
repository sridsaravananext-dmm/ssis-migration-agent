from __future__ import annotations

import json
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

def _run_migration(source_path, output_dir, mapping_path):
    from ssis_migration.migrate import run_migration
    return run_migration(
        source=str(source_path), output=str(output_dir),
        catalog=catalog.strip() or None,
        connection_mapping=str(mapping_path) if mapping_path else None,
        base_path=str(ROOT), execute=False, ai=ai_enabled,
    )

def _generate_ddl(metadata_path, catalog_name):
    from ssis_migration.ddl_generator import generate_ddl
    with metadata_path.open(encoding="utf-8") as f:
        metadata = json.load(f)
    return generate_ddl(metadata=metadata, catalog=catalog_name, column_mapping=True)

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_upload, tab_run, tab_artifacts, tab_ddl, tab_deploy = st.tabs([
    "📦 Upload & Configure", "🚀 Run Migration", "📂 Artifacts", "📋 DDL Preview", "📤 Deploy"])

# === TAB 1: Upload & Configure ===
with tab_upload:
    st.header("1. Select SSIS Package")
    package_mode = st.radio("Package source", ["Upload a .dtsx package", "Use WWI sample package"], horizontal=True)
    uploaded_package = None
    if package_mode == "Upload a .dtsx package":
        uploaded_package = st.file_uploader("Upload SSIS package (.dtsx)", type=["dtsx"])
    else:
        if SAMPLE_PACKAGE.is_file():
            st.success(f"WWI sample found: `{SAMPLE_PACKAGE.name}`")
        else:
            st.error("WWI sample not found. Upload a package instead.")

    st.divider()
    st.header("2. Connection Mapping & Metadata")
    mapping_mode = st.radio("Connection mapping source", ["Use existing repository mapping", "Upload mapping & metadata files"], horizontal=True)
    mapping_path = None

    if mapping_mode == "Use existing repository mapping":
        available_mappings = list(EXAMPLES.rglob("connection_mapping.json")) if EXAMPLES.exists() else []
        if available_mappings:
            mapping_choices = {str(p.relative_to(ROOT)): p for p in available_mappings}
            selected = st.selectbox("Select connection mapping", options=list(mapping_choices.keys()))
            mapping_path = mapping_choices[selected]
            with st.expander("View mapping JSON"):
                st.json(json.loads(mapping_path.read_text()))
        else:
            st.warning("No connection_mapping.json files found.")
    else:
        uploaded_mapping = st.file_uploader("Upload connection_mapping.json", type=["json"], key="mapping_upload")
        uploaded_metadata = st.file_uploader("Upload database metadata files", type=["json"], accept_multiple_files=True, key="metadata_upload")
        if uploaded_mapping is not None:
            upload_dir = RUN_ROOT / "uploads" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            upload_dir.mkdir(parents=True, exist_ok=True)
            mapping_path = upload_dir / uploaded_mapping.name
            mapping_path.write_bytes(uploaded_mapping.getvalue())
            if uploaded_metadata:
                for f in uploaded_metadata:
                    (upload_dir / f.name).write_bytes(f.getvalue())
            st.success(f"Mapping saved: `{mapping_path}`")

    st.divider()
    st.header("3. Summary")
    has_package = (package_mode == "Use WWI sample" and SAMPLE_PACKAGE.is_file()) or (uploaded_package is not None)
    can_run = has_package and mapping_path is not None
    c1, c2, c3 = st.columns(3)
    c1.metric("Package", "✅" if has_package else "❌")
    c2.metric("Mapping", "✅" if mapping_path else "❌")
    c3.metric("Ready", "✅" if can_run else "❌")

# === TAB 2: Run Migration ===
with tab_run:
    st.header("Run Migration Pipeline")
    if not can_run:
        st.info("Complete configuration in the **Upload & Configure** tab first.")
    else:
        st.markdown("Pipeline: Parse → IR → Analyze → Plan → Generate → Validate")
        if st.button("🚀 Run Migration", type="primary"):
            run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            run_dir = RUN_ROOT / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            try:
                source_path = SAMPLE_PACKAGE if package_mode == "Use WWI sample" else run_dir / uploaded_package.name
                if uploaded_package:
                    source_path.write_bytes(uploaded_package.getvalue())
                output_dir = run_dir / "artifacts"
                progress = st.progress(0, text="Starting...")
                with st.spinner("Running migration pipeline..."):
                    progress.progress(20, text="Parsing & analyzing...")
                    report = _run_migration(source_path, output_dir, mapping_path)
                    progress.progress(80, text="Generating artifacts...")
                    st.session_state.migration_result = report
                    st.session_state.migration_output = str(output_dir)
                    progress.progress(100, text="Done!")
                st.success("✅ Migration pipeline completed!")
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
        c3.metric("Migrated", report.get("migrated_package_count", 0))
        c4.metric("Failed", report.get("failed_package_count", 0))
        st.write(f"**Status:** `{report.get('status', 'UNKNOWN')}`")
        for package in report.get("packages", []):
            with st.expander(f"📦 {package.get('name', '?')} — {package.get('status', '?')}", expanded=True):
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
                        st.write(f"{'✅' if Path(path_str).exists() else '❌'} `{name}`")
                    for key, label in [("analysis", "Analysis"), ("plan", "Plan"), ("validation", "Validation")]:
                        fp = artifacts.get(key)
                        if fp and Path(fp).is_file():
                            with st.expander(f"View {label}"):
                                try: st.json(json.loads(Path(fp).read_text()))
                                except: st.code(Path(fp).read_text()[:5000])
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
    st.markdown("Generate CREATE TABLE DDL from database metadata for table provisioning.")
    metadata_files = list(EXAMPLES.rglob("database_metadata_*.json")) if EXAMPLES.exists() else []
    if not metadata_files:
        st.warning("No metadata files found.")
    else:
        metadata_choices = {str(p.relative_to(ROOT)): p for p in metadata_files}
        selected_metadata = st.selectbox("Metadata file", options=list(metadata_choices.keys()))
        metadata_path = metadata_choices[selected_metadata]
        with st.expander("Metadata summary"):
            try:
                meta = json.loads(metadata_path.read_text())
                st.write(f"**Database:** {meta.get('database', '?')}")
                st.write(f"**Tables:** {len(meta.get('tables', []))}")
            except: pass
        catalog_name = st.text_input("Catalog name", value=catalog)
        if st.button("Generate DDL", type="primary"):
            try:
                with st.spinner("Generating..."):
                    ddl_text, ddl_report = _generate_ddl(metadata_path, catalog_name)
                    st.session_state.ddl_text = ddl_text
                    st.session_state.ddl_report = ddl_report
                st.success("✅ DDL generated!")
            except Exception as exc:
                st.error(f"Failed: {exc}")
                with st.expander("Error"): st.exception(exc)
        ddl_text = st.session_state.ddl_text
        ddl_report = st.session_state.ddl_report
        if ddl_text:
            st.divider()
            if ddl_report:
                c1, c2, c3 = st.columns(3)
                c1.metric("Tables", ddl_report.get("generated_table_count", 0))
                c2.metric("Status", ddl_report.get("status", "?"))
                c3.metric("Findings", len(ddl_report.get("review_items", [])))
                if ddl_report.get("review_items"):
                    with st.expander("⚠️ Review findings"):
                        for item in ddl_report["review_items"]:
                            st.write(f"• `{item.get('object', '?')}` -> {item.get('issue', '?')}")
            create_count = len(re.findall(r"CREATE\s+TABLE", ddl_text, re.IGNORECASE))
            st.caption(f"{create_count} CREATE TABLE statements")
            st.code(ddl_text, language="sql")
            st.download_button("📥 Download DDL", data=ddl_text.encode(), file_name="generated_dw_ddl.sql")

# === TAB 5: Deploy ===
with tab_deploy:
    st.header("Deploy DABs Bundle")
    st.markdown("Deploy the generated DABs bundle. Requires `databricks` CLI.")
    output_value = st.session_state.migration_output
    if not output_value or not Path(output_value).is_dir():
        st.info("Run a migration first.")
    else:
        dabs_files = list(Path(output_value).rglob("databricks.yml")) + list(Path(output_value).rglob("databricks.yaml"))
        if not dabs_files:
            st.warning("No DABs files found.")
        else:
            for dabs_path in dabs_files:
                pkg_name = dabs_path.parent.name
                st.write(f"**Package:** `{pkg_name}`")
                with st.expander("View databricks.yml"):
                    st.code(dabs_path.read_text(), language="yaml")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button(f"✅ Validate ({pkg_name})", key=f"val_{pkg_name}"):
                        try:
                            r = subprocess.run(["databricks", "bundle", "validate"], cwd=str(dabs_path.parent), capture_output=True, text=True, timeout=30)
                            if r.returncode == 0: st.success("Validation passed!")
                            else: st.error("Failed!")
                            st.code(r.stdout or r.stderr)
                        except FileNotFoundError: st.error("`databricks` CLI not found.")
                        except Exception as e: st.error(str(e))
                with c2:
                    if st.button(f"📤 Deploy ({pkg_name})", type="primary", key=f"dep_{pkg_name}"):
                        try:
                            r = subprocess.run(["databricks", "bundle", "deploy"], cwd=str(dabs_path.parent), capture_output=True, text=True, timeout=60)
                            if r.returncode == 0:
                                st.success("✅ Deployed!")
                                st.info("Check Jobs UI to run the job.")
                            else: st.error("Deploy failed!")
                            st.code(r.stdout or r.stderr)
                        except FileNotFoundError: st.error("`databricks` CLI not found.")
                        except Exception as e: st.error(str(e))
                st.divider()

st.divider()
st.caption("SSIS -> Databricks Migration Accelerator | Review artifacts before deployment.")
