"""SSIS conversion and assessment entry points. Generated does not mean verified."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from ssis_migration.config import AssessmentConfig, dependency_paths


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip()).strip("_") or "conversion"


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    temporary.replace(path)


def object_to_dict(value):
    if hasattr(value, "to_dict"): return value.to_dict()
    if hasattr(value, "__dataclass_fields__"): return asdict(value)
    if isinstance(value, dict): return {str(k): object_to_dict(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [object_to_dict(v) for v in value]
    return value


def discover_packages(source):
    source = Path(source)
    if not source.exists(): raise FileNotFoundError(f"Source does not exist: {source}")
    if source.is_file():
        if source.suffix.lower() != ".dtsx": raise ValueError("Expected a .dtsx file")
        return [source]
    packages = sorted(p for p in source.rglob('*') if p.is_file() and p.suffix.lower() == '.dtsx')
    if not packages: raise FileNotFoundError(f"No .dtsx packages found under {source}")
    return packages


def analyze_one_package(package_path, procedures_path=None, *, procedures_paths=(), default_schema='dbo', dependency_scopes=None):
    from ssis_migration.parser.package_parser import SSISPackageParser
    from ssis_migration.ir.builder import IRBuilder
    from ssis_migration.analyzer import analyze_package
    from ssis_migration.procedure_file_loader import load_scoped_procedure_files
    definitions, _ = load_scoped_procedure_files(dependency_paths(procedures_path, procedures_paths), dependency_scopes, default_schema)
    definitions = {k:v for k,v in definitions.items() if '|' not in k}
    parsed = SSISPackageParser(package_path).parse()
    ir = IRBuilder().build(parsed)
    return {'parsed': parsed, 'package_ir': ir,
            'analysis': analyze_package(ir.to_dict(), procedure_bodies=definitions)}


def _run_ai_assisted_generation(
    plan_dict: dict[str, Any],
    all_artifacts: list[Any],
    artifacts_dir: Path,
    validation_results: list[dict[str, Any]],
    *,
    ai_provider: str = "databricks",
) -> list[dict[str, Any]]:
    """
    Call the LLM orchestrator for AI_ASSISTED steps.

    This is a per-item fallback: only steps whose deterministic
    code is a placeholder are sent to the LLM. Steps that already
    have real generated code are left untouched.

    Returns additional validation results for the
    LLM-generated code.
    """
    from ssis_migration.agent.provider_factory import (
        create_provider,
    )
    from ssis_migration.agent.orchestrator import (
        AIMigrationOrchestrator,
    )

    if not any(step.get("metadata", {}).get("migration_strategy", {}).get("strategy") == "AI_ASSISTED"
               for step in plan_dict["steps"]):
        return []
    provider = create_provider(ai_provider)
    orchestrator = AIMigrationOrchestrator(provider)

    artifacts_by_step = {
        a.source_step_id: a for a in all_artifacts
    }

    ai_validation: list[dict[str, Any]] = []

    for step in plan_dict["steps"]:
        strategy = (
            step.get("metadata", {})
            .get("migration_strategy", {})
            .get("strategy", "")
        )

        if strategy != "AI_ASSISTED":
            continue

        step_id = step["step_id"]
        step_name = step["name"]
        artifact = artifacts_by_step.get(step_id)

        if artifact is None:
            continue

        # Per-item fallback: only invoke the LLM when the
        # deterministic path produced a placeholder. If the
        # artifact already has real generated code, skip it.
        from ssis_migration.validator.native_spark import validate_native_spark
        if validate_native_spark(artifact.code, language=artifact.language)['status'] == 'PASS':
            continue

        # Extract procedure definitions from the
        # step metadata — both source and destination.
        intent = step.get("metadata", {}).get(
            "migration_intent", {}
        )
        proc_defs: dict[str, str] = {}
        for proc in intent.get("source_procedures", []):
            name = proc.get("procedure", "")
            definition = proc.get("definition", "")
            if name and definition:
                proc_defs[name] = definition
        for proc in intent.get("destination_procedures", []):
            name = proc.get("procedure", "")
            definition = proc.get("definition", "")
            if name and definition:
                proc_defs[name] = definition

        try:
            result = orchestrator.run(
                migration_step=step,
                procedure_definitions=proc_defs
                or None,
            )
        except Exception as exc:
            ai_validation.append({
                "step_id": step_id,
                "step_name": step_name,
                "validator": "ai_orchestrator",
                "status": "ERROR",
                "error": str(exc),
            })
            continue

        if result.status == "GENERATED" and result.code:
            # Replace the AI contract with
            # LLM-generated code.
            artifact.code = result.code
            artifact.metadata["ai_generated"] = True
            artifact.metadata["ai_status"] = (
                result.status
            )
            artifact.metadata["ai_assumptions"] = (
                result.assumptions
            )
            artifact.metadata["ai_unresolved"] = (
                result.unresolved_items
            )

            # Validate the LLM-generated code.
            from ssis_migration.agent.code_validator import (
                validate_generated_code,
            )

            code_result = validate_generated_code(
                result.code, step_name
            )
            ai_validation.append({
                "step_id": step_id,
                "step_name": step_name,
                "validator": "ai_orchestrator",
                "status": result.status,
                "code_validation": code_result,
                "assumptions": result.assumptions,
                "unresolved_items": (
                    result.unresolved_items
                ),
            })
        else:
            ai_validation.append({
                "step_id": step_id,
                "step_name": step_name,
                "validator": "ai_orchestrator",
                "status": result.status,
                "unresolved_items": (
                    result.unresolved_items
                ),
            })

    return ai_validation


def generate_migration_artifacts(package_ir, analysis, connection_mapping_path, base_path, package_output,
        *, ai=False, ai_provider='databricks', catalog=None, package_name='', ddl_path=None,
        procedures_path=None, ddl_paths=(), procedures_paths=(), config=None):
    from ssis_migration.context.builder import build_migration_context
    from ssis_migration.database.connection_mapping_loader import load_connection_mapping
    from ssis_migration.procedure_file_loader import load_scoped_procedure_files
    from ssis_migration.planner.planner import MigrationPlanner
    from ssis_migration.generator.dispatcher import GeneratorDispatcher
    from ssis_migration.generator.operations import MigrationOperationBuilder
    from ssis_migration.generator.databricks_operations import DatabricksOperationGenerator
    from ssis_migration.generator.notebook import NotebookAssembler
    from ssis_migration.analyzer import analyze_package
    from ssis_migration.assessment import finding, assess_operations, assessment_summary
    from ssis_migration.validator.native_spark import validate_native_spark

    config = config or AssessmentConfig(catalog=catalog or 'main')
    package_output = Path(package_output)
    package_output.mkdir(parents=True, exist_ok=True)
    definitions, origins = load_scoped_procedure_files(dependency_paths(procedures_path, procedures_paths), config.dependency_scopes, config.default_schema)
    ddls = dependency_paths(ddl_path, ddl_paths)
    context = build_migration_context(package_ir, analysis,
        load_connection_mapping(connection_mapping_path) if connection_mapping_path else None,
        base_path, ddl_paths=ddls, procedure_definitions=definitions,
        procedure_origins=origins, default_schema=config.default_schema, dependency_scopes=config.dependency_scopes)
    context.metadata.update(config.to_dict())
    # Classification must not choose the first same-named procedure across databases.
    from ssis_migration.analyzer.sql_task_classifier import classify_execute_sql_tasks
    procedure_contexts = {}
    for db in context.databases:
        procedure_contexts[db.connection_reference] = {f'{obj.schema}.{obj.name}': obj.definition
            for obj in db.database_ir.objects if obj.definition and 'PROCEDURE' in obj.object_type.upper()}
    unique = next(iter(procedure_contexts.values())) if len(procedure_contexts) == 1 else {}
    context.ssis_analysis = analyze_package(package_ir.to_dict(), procedure_bodies=unique)
    context.ssis_analysis['sql_tasks'] = classify_execute_sql_tasks(package_ir.to_dict()['executables'],
        procedure_contexts=procedure_contexts, connection_aliases=context.metadata.get('connection_aliases', {}))
    analysis = context.ssis_analysis
    plan = MigrationPlanner().build_plan(context)
    plan.metadata.update(config.to_dict())
    plan.metadata['generation_method'] = 'DETERMINISTIC_GRAPH' if plan.metadata.get('planner') == 'source_graph' else 'LEGACY_WITH_OPTIONAL_AI'
    if plan.metadata.get('planner') != 'source_graph':
        plan.metadata['uses_legacy_controls'] = any(s.step_type != 'REVIEW_ONLY' for s in plan.steps)
        if config.target_schema:
            plan.unresolved.append(finding('TARGET_MAPPING_REVIEW_REQUIRED', 'target_schema',
                'Legacy generation does not apply physical per-output mappings. Use graph planning.', evidence=config.target_schema))
        if config.layer.casefold() in {'multi-layer','multilayer'}:
            plan.unresolved.append(finding('LAYER_MAPPING_REQUIRED', 'target_layer', 'Legacy generation requires per-output layer review.'))
    plan_dict = plan.to_dict()
    artifacts, operations_by_step, ai_results = [], {}, []
    if not config.dry_run:
        dispatcher, builder, generator = GeneratorDispatcher(), MigrationOperationBuilder(), DatabricksOperationGenerator()
        for step in plan_dict['steps']:
            sid = step['step_id']
            if plan.metadata.get('planner') == 'source_graph':
                operations_by_step[sid] = plan.metadata['operations_by_step'][sid]
                continue
            if step['step_type'] == 'REVIEW_ONLY':
                operations_by_step[sid] = [{'operation_id': 'manual_implementation', 'code': '# REVIEW_REQUIRED: unsupported source work', 'order': 0}]
                continue
            try:
                artifact = dispatcher.generate(step)
                artifacts.append(artifact)
                operations = builder.build(step)
            except Exception as exc:
                operations_by_step[sid] = [{'operation_id': 'generation', 'error': str(exc), 'order': 0}]
                continue
            results = []
            for operation in operations:
                row = {'operation_id': operation.operation_id, 'operation_type': operation.operation_type, 'order': operation.order}
                try: row['code'] = generator.generate_operation(operation.to_dict())
                except Exception as exc: row['error'] = str(exc)
                results.append(row)
            operations_by_step[sid] = results
        if ai:
            try:
                ai_results = _run_ai_assisted_generation(plan_dict, artifacts, package_output, [], ai_provider=ai_provider)
            except Exception as exc:
                ai_results = [{'step_id': 'ai_provider', 'status': 'ERROR', 'error': str(exc)}]
        validations, operation_findings = assess_operations(operations_by_step, artifacts)
        plan.unresolved.extend(operation_findings)
        for result in ai_results:
            if result.get('status') != 'GENERATED' or result.get('unresolved_items') or result.get('assumptions'):
                plan.unresolved.append(finding('AI_REVIEW_REQUIRED', result['step_id'],
                    'AI generation has errors, assumptions, or unresolved items; inspect validation_results.json.', evidence=result.get('status')))
    else:
        validations = []
    # Stable identities allow repeated runs to update the same finding rather than duplicate it.
    findings = list({f['finding_id']: f for f in plan.unresolved}.values())
    plan.unresolved = findings
    pending_items = []
    for sid, ops in plan.metadata.get('operations_by_step', operations_by_step).items():
        for op in ops:
            if op.get('pending_item'):
                pending_items.append({**op['pending_item'], 'step_id':sid})
    plan.metadata['pending_items'] = pending_items
    for step in plan.steps:
        step.metadata['pending_ids'] = [p['pending_id'] for p in pending_items if p['step_id'] == step.step_id]
    inventory = plan.metadata['source_inventory']
    summary = assessment_summary(inventory, validations, findings, dry_run=config.dry_run)
    summary['generation']['method'] = plan.metadata['generation_method']
    summary['generation']['ai_requested'] = ai
    summary['generation']['ai_result_count'] = len(ai_results)
    notebook = None
    if not config.dry_run:
        # Never embed rejected operation bodies in a deliverable, even in normal mode.
        failures = {(v['step_id'], v['operation_id']) for v in validations if v['status'] not in {'PASS','PENDING'}}
        safe_operations = {}
        for sid, ops in operations_by_step.items():
            safe_operations[sid] = [dict(op, code="raise RuntimeError('Operation requires manual implementation; see assessment.json.')", error=None)
                if (sid, op['operation_id']) in failures else op for op in ops]
            for op in safe_operations[sid]:
                if op.get('error') is None: op.pop('error', None)
        for artifact in artifacts:
            if (artifact.source_step_id, 'ai_implementation') in failures:
                artifact.metadata['ai_generated'] = False
                artifact.code = '# REVIEW_REQUIRED: generated implementation did not pass validation'
        assembler = NotebookAssembler()
        assembled = assembler.assemble(plan.to_dict(), artifacts, safe_operations)
        assembled['metadata']['assessment'] = {'status': summary['status'], 'verification': 'NOT_MEASURED'}
        for idx, cell in enumerate(assembled['cells']):
            if cell['cell_type'] == 'code':
                checked = validate_native_spark(''.join(cell['source']))
                rejected_cell = False
                for issue in checked['issues']:
                    # The fail-closed runtime gate is deliberate, not an incomplete code body.
                    if issue['code'] == 'INCOMPLETE_IMPLEMENTATION' and 'raise RuntimeError' in ''.join(cell['source']): continue
                    findings.append(finding(issue['code'], f'notebook:cell:{idx}', issue['message'], evidence=issue.get('line')))
                    rejected_cell = True
                if rejected_cell:
                    assembled['cells'][idx] = assembler._code_cell(
                        "raise RuntimeError('Notebook cell did not pass static validation; inspect assessment.json.')")
        if len(findings) != summary['human_review_item_count']:
            summary = assessment_summary(inventory, validations, findings)
            summary['generation'].update(method=plan.metadata['generation_method'], ai_requested=ai, ai_result_count=len(ai_results))
        assembled['metadata']['assessment']['status'] = summary['status']
        if any(f['severity'] == 'ERROR' for f in findings) and not any(
                "raise RuntimeError('Conversion has unresolved findings." in ''.join(c.get('source', []))
                for c in assembled['cells']):
            assembled['cells'].insert(2, assembler._code_cell(
                "raise RuntimeError('Conversion has unresolved findings. Resolve assessment.json findings and regenerate before execution.')"))
        # Graph drafts are review artifacts, including in strict mode. Strict CLI
        # still returns nonzero for review-required runs; execution remains gated.
        if plan.metadata.get('planner') == 'source_graph' or not (config.strict_mode and any(f['severity'] == 'ERROR' for f in findings)):
            notebook = package_output / f'{package_name or safe_name(package_ir.package_name or "package")}_Migration.ipynb'
            assembled['metadata']['assessment']['execution_readiness'] = execution_readiness(findings)
            write_json(notebook, assembled)
    summary['planning'] = {'pending_section_count':len(pending_items)}
    summary['execution_readiness'] = execution_readiness(findings)
    plan.metadata['execution_readiness'] = summary['execution_readiness']
    dependency_inventory = {'connections': [{'connection_reference': db.connection_reference, 'database': db.database_name,
        'role': db.role, 'procedures': [{'name': f'{obj.schema}.{obj.name}', 'definition_available': bool(obj.definition),
            'definition_sha256': hashlib.sha256((obj.definition or '').encode()).hexdigest(),
            'definition_files': obj.metadata.get('definition_files', []), 'dependencies': obj.dependencies}
            for obj in db.database_ir.objects if 'PROCEDURE' in obj.object_type.upper()]} for db in context.databases],
        'connection_aliases': context.metadata.get('connection_aliases', {})}
    verification_plan = {'status': 'NOT_EXECUTED', 'legacy_match_percent': None,
        'task_graph_reference': 'task_graph.json', 'dataflow_graph_reference': 'dataflow_graphs.json',
        'physical_objects': plan.metadata.get('mapping_uses', []),
        'required_checks': ['schema_and_types', 'keyed_rows_and_duplicates', 'null_and_error_behavior',
                            'conditional_output_routing', 'numeric_and_timestamp_boundaries',
                            'target_write_semantics', 'failure_and_rerun_behavior'],
        'note': 'Requirements only. No dataset comparison, legacy execution, or Spark execution has been performed.'}
    for name, data in [('package_ir', package_ir.to_dict()), ('analysis', analysis), ('migration_plan', plan.to_dict()),
                       ('validation_results', validations + ai_results), ('source_inventory', inventory), ('task_graph', plan.metadata.get('task_graph', {})),
                       ('dataflow_graphs', plan.metadata.get('dataflow_graphs', [])),
                       ('dependency_inventory', dependency_inventory), ('verification_plan', verification_plan),
                       ('pending_items', pending_items),
                       ('assessment', {**summary, 'findings': findings}), ('operations', operations_by_step)]:
        write_json(package_output / f'{name}.json', data)
    review = package_output / f'{package_name or safe_name(package_ir.package_name or "package")}_review.md'
    review.write_text(_review_text(package_ir.package_name, summary, findings), encoding='utf-8')
    return {'notebook_file': str(notebook) if notebook else None, 'review_file': str(review),
            'artifact_count': len(artifacts), 'assessment': summary, 'findings': findings,
            'notebook_status': 'DRY_RUN' if config.dry_run else ('BLOCKED' if notebook is None else
                'DRAFT_REQUIRES_REVIEW' if any(f['severity'] == 'ERROR' for f in findings) else 'GENERATED_FOR_REVIEW')}


def execution_readiness(findings):
    blockers = [f['finding_id'] for f in findings if f['severity'] == 'ERROR']
    return {'status':'BLOCKED_BY_REVIEW' if blockers else 'STATIC_CHECKS_PASSED',
        'blocking_finding_ids':blockers, 'runtime_verification':'NOT_MEASURED'}


def _review_text(name, summary, findings):
    generation = summary['generation']
    checks = ('Static code checks: NOT RUN (dry run).' if generation.get('status') == 'NOT_RUN' else
        f"Operations passing static checks: {generation['statically_valid_operations']} / {generation['operation_count']}")
    lines = [f'# Conversion assessment: {name}', '', f"Status: {summary['status']}",
             'Legacy behavioral match: NOT MEASURED. No runtime equivalence percentage is claimed.', '',
             f"Source items: {summary['source_accounting']['total_items']}",
             checks,
             f"Container/control bookkeeping operations (excluded): {generation.get('structural_operation_count', 0)}",
             f"Pending implementation sections: {summary['planning']['pending_section_count']}",
             f"Execution readiness: {summary['execution_readiness']['status']}",
             'Read this review and the notebook draft first. JSON files are diagnostic records, not scripts to run.',
             'Operation coverage is a static generation metric, not legacy accuracy.', '', '## Human review findings', '']
    lines.extend(f"- **{f['code']}** (`{f['source_id']}`): {f['message']}" for f in findings)
    if not findings: lines.append('No static findings. Runtime comparison is still required.')
    return '\n'.join(lines)


def run_migration(source, output, *, catalog=None, connection_mapping=None, base_path=None, execute=False,
        ai=False, ai_provider='databricks', ddl_path=None, procedures_path=None, ddl_paths=(), procedures_paths=(),
        layer='unspecified', target_schema=None, description='', default_schema='dbo', strict_mode=False,
        dry_run=False, respect_human_edits=True, planner_mode='graph', object_mappings=None, dependency_scopes=None):
    from ssis_migration.lifecycle import input_manifest, notebook_protection_reason, file_record
    if execute: raise ValueError('Deployment and data execution are not implemented; use conversion/assessment only')
    config = AssessmentConfig(catalog=catalog or 'main', layer=layer, target_schema=target_schema,
        description=description, default_schema=default_schema, strict_mode=strict_mode, dry_run=dry_run,
        respect_human_edits=respect_human_edits, planner_mode=planner_mode,
        object_mappings=object_mappings or {}, dependency_scopes=dependency_scopes or {})
    procedures = dependency_paths(procedures_path, procedures_paths)
    ddls = dependency_paths(ddl_path, ddl_paths)
    known_dependency_files = {str(p) for p in procedures + ddls} | {p.name for p in procedures + ddls}
    if any(key not in known_dependency_files for key in config.dependency_scopes):
        raise ValueError('Dependency scopes must reference supplied procedure/DDL files')
    source_path, output_path = Path(source).expanduser().resolve(), Path(output).expanduser().resolve()
    packages = discover_packages(source_path)
    resolved_base = Path(base_path).resolve() if base_path else Path.cwd()
    output_path.mkdir(parents=True, exist_ok=True)
    report = {'tool': 'ssis-migration-agent', 'run_id': uuid4().hex,
        'created_at_utc': datetime.now(timezone.utc).isoformat(), 'mode': 'DRY_RUN' if dry_run else 'GENERATE_AND_ASSESS',
        'target': {'platform': 'databricks', **config.to_dict()}, 'source': str(source_path), 'output': str(output_path),
        'package_count': len(packages), 'packages': [], 'deployment': {'status': 'NOT_IMPLEMENTED'}}
    for package in packages:
        name = safe_name(package.stem)
        if sum(safe_name(p.stem).casefold() == name.casefold() for p in packages) > 1:
            name += '_' + hashlib.sha256(str(package.relative_to(source_path)).encode()).hexdigest()[:8]
        entry = {'package': str(package), 'name': package.stem, 'status': 'FAILED', 'artifacts': {}}
        # Keep diagnostics in a unique location if input fingerprinting fails.
        directory = output_path / name / ('errors_' + report['run_id'])
        try:
            mapping = Path(connection_mapping).expanduser().resolve() if connection_mapping else package.parent / 'connection_mapping.json'
            if connection_mapping and not mapping.is_file(): raise FileNotFoundError(f'Connection mapping not found: {mapping}')
            mapping = mapping if mapping.is_file() else None
            manifest = input_manifest(package, procedures, ddls, mapping, resolved_base, config, ai=ai and not dry_run, ai_provider=ai_provider)
            directory = output_path / name / manifest['fingerprint'][:20]
            directory.mkdir(parents=True, exist_ok=True)
            cache = directory / 'package_result.json'
            protection = notebook_protection_reason(directory) if respect_human_edits else None
            if protection:
                entry.update(status='REVIEW_REQUIRED', notebook_status='SKIPPED_HUMAN_REVIEWED',
                    message='Existing edited, reviewed, or untracked notebook was preserved. Revalidation is required before execution.',
                    protection_reason=protection, execution_readiness={'status':'REVALIDATION_REQUIRED','runtime_verification':'NOT_MEASURED'},
                    stages={'parsing':'NOT_RUN','analysis':'NOT_RUN','generation':'SKIPPED_HUMAN_REVIEWED','runtime_verification':'NOT_MEASURED'})
                for key, filename in [('manifest','migration_manifest.json'), ('notebook',f'{name}_Migration.ipynb'),
                        ('review',f'{name}_review.md'), ('pending_items','pending_items.json')]:
                    path = directory / filename
                    if path.is_file(): entry['artifacts'][key] = str(path)
            else:
                # Reassess deterministic input versions; avoid trusting stale/edited validation caches.
                # Remove only the prior generated notebook for this exact input version.
                # Reviewed/unreadable outputs took the protection branch above.
                previous = directory / f'{name}_Migration.ipynb'
                if previous.exists(): previous.unlink()
                analyzed = analyze_one_package(package, procedures_paths=procedures, default_schema=default_schema, dependency_scopes=config.dependency_scopes)
                result = generate_migration_artifacts(analyzed['package_ir'], analyzed['analysis'], mapping,
                    resolved_base, directory, ai=ai and not dry_run, ai_provider=ai_provider, catalog=catalog,
                    package_name=name, ddl_paths=ddls, procedures_paths=procedures, config=config)
                entry.update(status=result['assessment']['status'], assessment=result['assessment'],
                    notebook_status=result['notebook_status'], findings=result['findings'],
                    artifacts={'review': result['review_file'], 'manifest': str(directory / 'migration_manifest.json'),
                        'ir': str(directory / 'package_ir.json'), 'analysis': str(directory / 'analysis.json'),
                        'plan': str(directory / 'migration_plan.json'), 'validation': str(directory / 'validation_results.json'),
                        'assessment': str(directory / 'assessment.json'), 'source_inventory': str(directory / 'source_inventory.json'),
                        'task_graph': str(directory / 'task_graph.json'), 'dataflow_graphs': str(directory / 'dataflow_graphs.json'),
                        'dependency_inventory': str(directory / 'dependency_inventory.json'), 'verification_plan': str(directory / 'verification_plan.json')},
                    stages={'parsing': 'SUCCEEDED', 'analysis': 'SUCCEEDED',
                        'generation': 'NOT_RUN' if dry_run else result['notebook_status'], 'runtime_verification': 'NOT_MEASURED'})
                if result['notebook_file']: entry['artifacts']['notebook'] = result['notebook_file']
                entry['artifacts']['pending_items'] = str(directory / 'pending_items.json')
                manifest['artifacts'] = entry['artifacts']
                manifest['generated_notebooks'] = {Path(result['notebook_file']).name:file_record(result['notebook_file'])['sha256']} if result['notebook_file'] else {}
                write_json(directory / 'migration_manifest.json', manifest)
                write_json(cache, entry)
        except Exception as exc:
            write_json(directory / 'error.json', {'error': str(exc), 'traceback': traceback.format_exc()})
            entry.update(error=str(exc), error_file=str(directory / 'error.json'))
        report['packages'].append(entry)
    report['analyzed_package_count'] = sum(p.get('stages', {}).get('analysis') == 'SUCCEEDED' for p in report['packages'])
    report['generated_package_count'] = sum('notebook' in p['artifacts'] and p.get('notebook_status') != 'SKIPPED_HUMAN_REVIEWED' for p in report['packages'])
    report['preserved_package_count'] = sum(p.get('notebook_status') == 'SKIPPED_HUMAN_REVIEWED' for p in report['packages'])
    report['review_required_package_count'] = sum(p['status'] == 'REVIEW_REQUIRED' for p in report['packages'])
    report['failed_package_count'] = sum(p['status'] == 'FAILED' for p in report['packages'])
    report['verification'] = {'status': 'NOT_MEASURED', 'legacy_match_percent': None}
    report['status'] = ('FAILED' if report['failed_package_count'] == len(packages) else
        'PARTIAL_FAILURE' if report['failed_package_count'] else
        'REVIEW_REQUIRED' if report['review_required_package_count'] else 'ANALYZED' if dry_run else 'ARTIFACTS_GENERATED')
    write_json(output_path / 'migration_report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description='Generate native Spark artifacts and assess SSIS conversion coverage.')
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--catalog')
    parser.add_argument('--connection-mapping')
    parser.add_argument('--base-path')
    parser.add_argument('--procedure', dest='procedures_paths', action='append', default=[], help='Repeat for each procedure file')
    parser.add_argument('--ddl', dest='ddl_paths', action='append', default=[], help='Repeat for each DDL file')
    parser.add_argument('--layer', default='unspecified')
    parser.add_argument('--planner-mode', choices=['graph','legacy'], default='graph')
    parser.add_argument('--object-mappings', help='JSON file mapping source objects to fully qualified target objects')
    parser.add_argument('--dependency-scopes', help='JSON file mapping dependency file names to connection references')
    parser.add_argument('--target-schema')
    parser.add_argument('--description', default='')
    parser.add_argument('--default-schema', default='dbo')
    parser.add_argument('--strict-mode', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--ai', action='store_true')
    parser.add_argument('--ai-provider', default='databricks', choices=['databricks', 'openai', 'puter', 'mock'])
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.execute: parser.error('--execute is not available: deployment and execution are not implemented')
    values = vars(args).copy()
    for key in ('object_mappings','dependency_scopes'):
        values[key] = json.loads(Path(values[key]).read_text(encoding='utf-8-sig')) if values[key] else {}
    result = run_migration(**values)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result['failed_package_count'] or (args.strict_mode and result['review_required_package_count']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
