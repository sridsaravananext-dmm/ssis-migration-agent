"""Human-readable requests for real exported files; never generate source data."""
from __future__ import annotations


SOURCE_EXPORT_REQUIREMENTS = {
    'ssis': {
        'label': 'SSIS',
        'primary': 'Original .dtsx packages; include called child packages. If you have an .ispac, also supply its extracted .dtsx files or the original project.',
        'dependencies': 'Project/package parameters (.params), connection managers (.conmgr), .dtsConfig/configuration files, environment-variable mappings, table/view DDL and complete procedure/function SQL (.sql), and referenced C# scripts/projects plus custom assembly/reference details.',
        'context': 'SSIS/SQL Server versions, project deployment model, parameter values used for the selected run, connection aliases mapped to source/target databases and schemas, and package execution order.',
        'status': 'Existing DTSX analysis and Databricks generation are available, subject to dependency/capability checks. The current DTSX profile is fixture-verified for PackageFormatVersion 8. Direct .ispac ingestion and real SSIS Script Task runtime equivalence are not established.',
    },
    'tm1': {
        'label': 'IBM TM1 / Planning Analytics',
        'primary': 'Native TurboIntegrator process exports (.pro), cube rules/feeders (.rux), and actual MDX queries or saved view/subset definitions (.mdx or their native text export).',
        'dependencies': 'Readable Prolog, Metadata, Data and Epilog sections; process parameters/variables and datasource-column bindings; ordered cube dimensions; hierarchy/member exports with element types and consolidation weights; aliases/attributes; static subset member order or dynamic subset expressions; and view/filter definitions.',
        'context': 'TM1/Planning Analytics version, source cube/process names, datasource type, actual parameter values and record-order evidence where ordering matters. Include empty TI sections as exported rather than guessing their contents.',
        'status': 'MDX/rules/TI analysis and bounded Databricks candidates exist. The current runnable route requires a versioned source bundle, metadata and explicit target mapping. Native serialized .pro decoding, general feeders/consolidations and process lifecycle translation remain unfinished. Supply original exports plus readable sections; do not manufacture missing metadata to satisfy the bundle schema.',
    },
    'informatica': {
        'label': 'Informatica PowerCenter',
        'primary': 'Native PowerCenter object/repository export (.xml), ideally one complete workflow exported with its dependencies.',
        'dependencies': 'Workflows/worklets, sessions, mappings/mapplets, source/target definitions, reusable transformations and shortcut targets; actual parameter files (.par or the extension used by your project); connection definitions; SQL overrides and called procedure/function SQL; and referenced command scripts.',
        'context': 'PowerCenter/repository version, database technologies, source/target schemas, session/configuration overrides, parameter precedence and connection aliases. Export dependent objects instead of supplying only a workflow shell.',
        'status': 'Bounded POWERMART XML structural analysis, dependency/column links and review contracts exist. PowerCenter-to-Databricks transformation generation and version-specific equivalence are not implemented yet.',
    },
    'bods': {
        'label': 'SAP Data Services / BODS',
        'primary': 'Native ATL export (.atl) for a complete job with dependent objects. Include a native XML export as well if your version provides one; keep the original ATL.',
        'dependencies': 'Jobs, workflows, dataflows, datastores, source/target table definitions, file formats, custom functions, scripts, global/substitution variables and parameter/configuration exports; SQL overrides, lookup definitions and called procedure SQL.',
        'context': 'SAP Data Services/repository version, datastore/database technologies, configuration selected for the run, source/target bindings, and text-file encoding/delimiter/null/type definitions.',
        'status': 'Only a bounded DI XML structural profile exists. Native ATL parsing and BODS Databricks generation are not implemented. ATL is requested to implement its real format; do not rewrite it into the synthetic DI XML profile.',
    },
    'odi': {
        'label': 'Oracle Data Integrator / ODI',
        'primary': 'Native design-time Smart Export/object export (.xml) with dependencies: mappings for ODI 12c+ or interfaces for 11g, plus the relevant packages/load plans. Include native component XML exports if separate.',
        'dependencies': 'Models/datastores and column metadata; procedures, variables and sequences; referenced/custom Knowledge Modules (IKM/LKM/CKM and others actually used); contexts and logical/physical schema mappings; options, SQL/PLSQL and any external Groovy/Python/shell scripts.',
        'context': 'Exact ODI/repository version, technology versions, selected context, Knowledge Module options, and logical-to-physical source/target mappings. Scenarios alone may omit the design-time behavior needed for translation.',
        'status': 'A bounded Sunopsis object/field structural adapter exists. Modern mapping classes, Knowledge Module semantics, other export variants and ODI Databricks generation remain unfinished and must be checked against the actual export.',
    },
}


def source_export_guide(platform: str | None = None) -> str:
    """Describe requested files separately from currently implemented support."""
    if platform is not None and platform not in SOURCE_EXPORT_REQUIREMENTS:
        raise ValueError('Choose SSIS, TM1, PowerCenter, BODS or ODI; DataStage and Ab Initio are excluded.')
    selected = SOURCE_EXPORT_REQUIREMENTS.items() if platform is None else [(platform,SOURCE_EXPORT_REQUIREMENTS[platform])]
    lines = [
        '# Real source exports to supply',
        '',
        'Send original exports from an existing project, with their dependencies. No new synthetic packages, metadata, input rows or expected results will be authored for this work. Existing regression demonstrations are not proof of your workload behavior.',
        '',
        'You do not need all five platforms to start. Prefer one SSIS package/project with dependencies first, then one PowerCenter workflow export if available. Send other platforms as their exports become available. Databricks is the only current target; DataStage and Ab Initio are excluded.',
        '',
    ]
    for _, requirement in selected:
        lines.extend([
            '**'+requirement['label']+'**', '',
            '- Main files: '+requirement['primary'],
            '- Dependencies: '+requirement['dependencies'],
            '- Export/run context: '+requirement['context'],
            '- Current tool support: '+requirement['status'], '',
        ])
    lines.extend([
        '**Include with each source bundle**', '',
        '- A short description of the real job: inputs, outputs, execution order, incremental/watermark/CDC/SCD behavior, transaction/error handling, and mandatory business rules. State what is unknown rather than filling gaps with assumptions.',
        '- Existing exported DDL, procedure/package specifications AND bodies (Oracle .pks/.pkb or equivalent .sql), functions, views, referenced synonyms, dependencies and connection mappings. Supply metadata in the format you actually have (SQL, CSV, JSON, spreadsheet or native export); formats not yet understood will need inspection before adapter support is claimed.',
        '- For validation later: a small genuine or consistently sanitized input extract, its actual output from the existing working job, the parameters/input snapshot or watermark used, and source logs/row counts. Do not invent expected results. Design-time analysis can begin before these runtime observations are available.',
        '- Proposed Databricks catalog and bronze/silver/gold schemas, data storage paths, source/target table mappings and parameter bindings, when known. These target choices do not replace source definitions or infer missing business logic.',
        '- Keep folders, filenames and internal references intact in one ZIP per source project/job. The current browser ZIP route permits up to 256 files and 64 MiB of uncompressed content; start with one complete dependency chain. Keep original encodings and record the source tool/export version.',
        '- Omit passwords, tokens and private keys. Keep connection aliases, database technology and required schema/table identities; if names must be sanitized, change references consistently and describe the changes. No live legacy-server connection is required.', '',
        '**What happens next**', '',
        'Inventory the real files and identify format/version, dependencies and gaps; agree the behavior and acceptance requirements; extend one source adapter/capability at a time; build IR/contracts before code generation; validate against supplied source observations. Missing evidence stays unresolved. The manual Databricks review checkpoint is deferred, while execution, critical-behavior and at least 80% weighted verified semantic acceptance gates remain in force.',
        '',
    ])
    return '\n'.join(lines)
