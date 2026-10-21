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

def _run_migration(source_path, output_dir, mapping_path, procedures_path=None, ddl_path=None):
    from ssis_migration.migrate import run_migration
    return run_migration(
        source=str(source_path), output=str(output_dir),
        catalog=catalog.strip() or None,
        connection_mapping=str(mapping_path) if mapping_path else None,
        base_path=str(ROOT), execute=False, ai=ai_enabled,
        ai_provider="databricks",
        ddl_path=str(ddl_path) if ddl_path else None,
        procedures_path=str(procedures_path) if procedures_path else None,
    )

def _classify_dependency(filename, content_bytes):
    """Auto-classify an uploaded dependency file by its content."""
    name = filename.lower()
    try:
        text = content_bytes.decode("utf-8", errors="replace")
    except Exception:
        return "unknown"
    upper = text.upper()
    if name.endswith(".json"):
        return "connection_mapping"
    if name.endswith(".sql") or name.endswith(".txt"):
        has_proc = bool(re.search(r"CREATE\s+(OR\s+ALTER\s+)?PROC", upper))
        has_table = bool(re.search(r"CREATE\s+TABLE", upper))
        if has_proc and has_table:
            return "mixed_sql"
        if has_proc:
            return "procedures"
        if has_table:
            return "ddl"
        return "sql_other"
    return "unknown"

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_upload, tab_run, tab_artifacts, tab_ddl = st.tabs([
    "📦 Upload & Configure", "🚀 Run Migration", "📂 Artifacts", "📋 DDL Preview"])

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
    st.header("2. Upload Dependencies (optional)")
    st.caption("Upload any dependency files — they are auto-classified by content. "
               "SQL files with `CREATE PROCEDURE` become procedure definitions; "
               "SQL files with `CREATE TABLE` become DDL; JSON files become connection mappings. "
               "Connection setup (catalogs/schemas) can also be done manually in Databricks.")
    uploaded_deps = st.file_uploader(
        "Upload dependency files (.sql, .json, .txt)",
        type=["sql", "json", "txt"],
        accept_multiple_files=True,
        key="deps_upload",
    )

    mapping_path = None
    procedures_path = None
    ddl_path = None

    if uploaded_deps:
        upload_dir = RUN_ROOT / "uploads" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        upload_dir.mkdir(parents=True, exist_ok=True)

        st.write("**Detected dependencies:**")
        for f in uploaded_deps:
            ftype = _classify_dependency(f.name, f.getvalue())
            saved = upload_dir / f.name
            saved.write_bytes(f.getvalue())

            label = {
                "procedures": "📋 Procedures",
                "ddl": "🏗 DDL",
                "connection_mapping": "🔗 Connection Mapping",
                "mixed_sql": "📋+🏗 Mixed (Procedures + DDL)",
                "sql_other": "📄 SQL (unclassified)",
                "unknown": "❓ Unknown",
            }.get(ftype, "❓ Unknown")

            st.write(f"- `{f.name}` → {label}")

            if ftype in ("procedures", "mixed_sql") and procedures_path is None:
                procedures_path = saved
            elif ftype in ("ddl", "mixed_sql") and ddl_path is None:
                ddl_path = saved
                st.session_state.ddl_text = saved.read_text(encoding="utf-8")
            elif ftype == "connection_mapping" and mapping_path is None:
                mapping_path = saved
            elif ftype == "sql_other":
                # Unclassified SQL: check if it has CREATE TABLE at least
                st.warning(f"`{f.name}` has SQL content but no CREATE PROCEDURE or CREATE TABLE detected.")

    st.divider()
    st.header("3. Summary")
    has_package = (package_mode == "Use WWI sample" and SAMPLE_PACKAGE.is_file()) or (uploaded_package is not None)
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
                source_path = SAMPLE_PACKAGE if package_mode == "Use WWI sample" else run_dir / uploaded_package.name
                if uploaded_package:
                    source_path.write_bytes(uploaded_package.getvalue())
                output_dir = run_dir / "artifacts"
                progress = st.progress(0, text="Starting...")
                with st.spinner("Running migration pipeline..."):
                    progress.progress(20, text="Parsing & analyzing...")
                    report = _run_migration(source_path, output_dir, mapping_path, procedures_path, ddl_path)
                    progress.progress(80, text="Generating notebook...")
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
