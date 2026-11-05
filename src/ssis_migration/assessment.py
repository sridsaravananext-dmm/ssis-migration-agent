"""Source accounting and evidence-based findings. Coverage is never accuracy."""
from __future__ import annotations
import hashlib
import re
from collections import defaultdict
from ssis_migration.sql_text import has_dynamic_call, normalize_name, procedure_calls
from ssis_migration.planner.models import MigrationStep
from ssis_migration.validator.native_spark import validate_native_spark


def finding(code, source_id, message, *, severity='ERROR', evidence=None):
    identity = '\0'.join((code, source_id, str(evidence or '')))
    return {'finding_id': hashlib.sha256(identity.encode()).hexdigest()[:24],
            'code': code, 'source_id': source_id, 'severity': severity,
            'message': message, 'evidence': evidence, 'review_status': 'OPEN'}


def account_source(context, plan):
    """Preserve legacy loads; explicitly account for anything they do not plan."""
    package = context.package_ir
    findings, inventory = [], []
    load_refs = {}
    for step in plan.steps:
        # Existing loads are grouped by sequence name, but record the precise origin.
        candidates = [e for e in package.executables if e.name == step.name and e.executable_type == 'STOCK:SEQUENCE']
        if len(candidates) == 1:
            load_refs[candidates[0].ref_id] = step.step_id
            step.metadata['source_ref_id'] = candidates[0].ref_id
        findings.append(finding('LOGICAL_LOAD_REVIEW_REQUIRED', step.step_id,
            'Legacy load rules use task-name/control-table conventions; verify the grouped source semantics.', severity='WARNING'))
    def owner(ref):
        matches = [(key, value) for key, value in load_refs.items() if key and ref and (ref == key or ref.startswith(key + '\\'))]
        return max(matches, key=lambda x: len(x[0]))[1] if matches else None
    for idx, exe in enumerate(package.executables):
        ref = exe.ref_id or f'executable:{idx}:{exe.name}'
        step_id = owner(ref)
        review_only = step_id is None
        container = bool(exe.executable_type and ('SEQUENCE' in exe.executable_type.upper() or 'LOOP' in exe.executable_type.upper()))
        if not step_id:
            step_id = 'review_' + hashlib.sha256(ref.encode()).hexdigest()[:12]
            plan.steps.append(MigrationStep(step_id=step_id, name=exe.name or ref,
                step_type='REVIEW_ONLY', metadata={'source_ref_id': ref,
                'migration_strategy': {'strategy': 'REVIEW_REQUIRED'}, 'source_type': exe.executable_type}))
            findings.append(finding('UNPLANNED_SOURCE_WORK', ref,
                'This executable is retained for manual implementation; no supported generic translator is available.'))
        inventory.append({'source_id': ref, 'name': exe.name, 'kind': 'CONTAINER' if container else 'TASK',
                          'step_id': step_id, 'disposition': 'REVIEW_ONLY' if review_only else 'PLANNED_CANDIDATE'})
    for fidx, flow in enumerate(package.data_flows):
        for cidx, comp in enumerate(flow.components):
            ref = comp.ref_id or f'flow:{fidx}:component:{cidx}:{comp.name}'
            step_id = owner(ref)
            inventory.append({'source_id': ref, 'name': comp.name, 'kind': 'COMPONENT', 'step_id': step_id,
                              'disposition': 'PLANNED_CANDIDATE' if step_id else 'REVIEW_ONLY'})
            if not step_id:
                findings.append(finding('UNPLANNED_SOURCE_WORK', ref, 'Data-flow component has no generated logical load.'))
            # Analysis recognition alone does not prove component implementation.
            if comp.detected_type not in {'OLE_DB_SOURCE', 'OLE_DB_DESTINATION'}:
                findings.append(finding('TRANSFORMATION_REVIEW_REQUIRED', ref,
                    'Verify native implementation of this component; recognition is not conversion evidence.', evidence=comp.detected_type))
        outgoing, incoming = defaultdict(int), defaultdict(int)
        for path in flow.paths:
            start = path.get('start_id') or path.get('startId') or path.get('attributes', {}).get('startId')
            end = path.get('end_id') or path.get('endId') or path.get('attributes', {}).get('endId')
            if start: outgoing[start] += 1
            if end: incoming[end] += 1
        if any(v > 1 for v in [*outgoing.values(), *incoming.values()]) or sum(c.detected_type == 'OLE_DB_SOURCE' for c in flow.components) > 1:
            findings.append(finding('DATAFLOW_ROUTING_REVIEW_REQUIRED', flow.ref_id or flow.name or str(fidx),
                'Multiple inputs/branches need explicit graph-aware verification before execution.'))
    for idx, edge in enumerate(package.precedence_constraints):
        if edge.expression or edge.value not in {None, 'Success'} or edge.logical_and is False:
            findings.append(finding('CONTROL_FLOW_NOT_PRESERVED', edge.ref_id or f'precedence:{idx}',
                'Conditional, failure, completion, or OR precedence needs outcome-aware implementation.'))
    if not inventory:
        findings.append(finding('NO_SOURCE_WORK', package.package_name or 'package', 'No executable source work was extracted.'))
    findings.extend(dependency_findings(context))
    source_ids = [item['source_id'] for item in inventory]
    for source_id in set(source_ids):
        if source_ids.count(source_id) > 1:
            findings.append(finding('DUPLICATE_SOURCE_ID', source_id,
                'Multiple source elements share an identity; resolve before associating generated operations.'))
    plan.metadata['source_inventory'] = inventory
    plan.unresolved.extend(findings)
    return inventory, findings


def dependency_findings(context):
    from ssis_migration.dependencies import dependency_findings as inspect
    return inspect(context)


def assess_operations(operations_by_step, artifacts):
    results, findings = [], []
    artifacts_by_id = {a.source_step_id: a for a in artifacts}
    for step_id, ops in operations_by_step.items():
        artifact = artifacts_by_id.get(step_id)
        if artifact and artifact.metadata.get('ai_generated'):
            ops = [{'operation_id': 'ai_implementation', 'code': artifact.code}]
        if not ops:
            ops = [{'operation_id': 'missing_implementation', 'code': ''}]
        for op in ops:
            ref = f'{step_id}:{op["operation_id"]}'
            checked = validate_native_spark(op.get('code', ''))
            if op.get('pending_item') and op.get('operation_type') in {'REVIEW_ONLY','WRITE_PENDING'}:
                # Preserve the named draft section, but never count it as implemented.
                issues = [i for i in checked['issues'] if i['code'] != 'INCOMPLETE_IMPLEMENTATION']
                if not issues and not op.get('error'):
                    results.append({'step_id':step_id, 'operation_id':op['operation_id'],
                        'status':'PENDING', 'issues':[], 'pending_id':op['pending_item']['pending_id']})
                    continue
            if op.get('error'):
                checked = {'status': 'REJECTED', 'issues': [{'code': 'OPERATION_GENERATION_FAILED', 'severity': 'ERROR', 'message': op['error'], 'line': None}]}
            # Comments and metadata-only assignments cannot establish a data operation.
            if checked['status'] == 'PASS' and op.get('operation_type') == 'LOOKUP_UPDATE':
                checked = {'status': 'REJECTED', 'issues': [{'code': 'LOOKUP_IMPLEMENTATION_MISSING', 'severity': 'ERROR', 'message': 'Lookup metadata does not implement dimension key resolution.', 'line': None}]}
            results.append({'step_id': step_id, 'operation_id': op['operation_id'], **checked})
            for item in checked['issues']:
                findings.append(finding(item['code'], ref, item['message'], evidence=item.get('line')))
    return results, findings


def assessment_summary(inventory, validations, findings, *, dry_run=False):
    valid = sum(v['status'] == 'PASS' for v in validations)
    return {'source_accounting': {'total_items': len(inventory),
                'planned_candidates': sum(i['disposition'] == 'PLANNED_CANDIDATE' for i in inventory),
                'review_only_items': sum(i['disposition'] == 'REVIEW_ONLY' for i in inventory),
                'disabled_items': sum(i['disposition'] == 'DISABLED' for i in inventory)},
            'generation': {'status':'NOT_RUN' if dry_run else 'ASSESSED',
                'operation_count': len(validations), 'statically_valid_operations': valid,
                'pending_operations': sum(v['status'] == 'PENDING' for v in validations),
                'operation_coverage_percent': round(100 * valid / len(validations), 1) if validations else None,
                'basis': 'Operations passing native-code checks; not task coverage or behavioral accuracy.'},
            'verification': {'status': 'NOT_MEASURED', 'legacy_match_percent': None},
            'human_review_item_count': len(findings),
            'status': 'REVIEW_REQUIRED' if findings else ('ANALYZED' if dry_run else 'ARTIFACTS_GENERATED')}
