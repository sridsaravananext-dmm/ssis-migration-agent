"""Readable notebook evidence without dumping Python finding dictionaries."""
import json


def text(value):
    return str(value).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('|','&#124;').replace('`','&#96;').replace('\r',' ').replace('\n','<br>')


def overview(plan):
    blockers=[f for f in plan.get('unresolved',[]) if isinstance(f,dict) and f.get('severity')=='ERROR']
    status='BLOCKED_BY_REVIEW' if blockers else 'TARGET_RUNTIME_VERIFICATION_REQUIRED'
    result=f'\n**Execution readiness: {status}**\n\n'
    if blockers:
        result+=f'{len(blockers)} blocking findings must be resolved and the notebook regenerated before execution. '
    result+='Legacy behavior match: **NOT_MEASURED**. Static generation does not establish workload equivalence.\n'
    if blockers:result+='\nBlocking codes: '+', '.join('`'+text(c)+'`' for c in sorted({f['code'] for f in blockers}))+'.\n'
    meta=plan.get('metadata',{})
    required=[r for r in meta.get('runtime_variables',[]) if r.get('required_external_input')]
    if required:
        result+='\n### Required runtime inputs\n\nSet `ssis_runtime_values` with values of the declared types. Bare numeric SQL references need numeric text/numbers as declared; date/timezone and business-key meaning still require review.\n\n'
        result+='| Key | Type | Additional constraint |\n| --- | --- | --- |\n'
        for row in required:
            result+=f"| {text(row['key'])} | {text(row['kind'])} | {'numeric SQL value' if row.get('numeric_fragment') else 'source semantics'} |\n"
        result+='\nInput template — replace every null with a reviewed typed value; this template is not runnable:\n\n```json\n'+json.dumps({r['key']:None for r in required},indent=2,ensure_ascii=False)+'\n```\n'
    return result


def findings_table(findings):
    sections=[]
    for severity,label in [('ERROR','Blocking findings'),('WARNING','Review warnings')]:
        records=[f for f in findings if isinstance(f,dict) and f.get('severity')==severity]
        if records:
            sections.append(f'## {label} ({len(records)})\n\n| Code | Source | Action / finding |\n| --- | --- | --- |\n'+
                '\n'.join('| '+text(f['code'])+' | '+text(f['source_id'])+' | '+text(f['message'])+' |' for f in records))
    other=[f for f in findings if not isinstance(f,dict) or f.get('severity') not in {'ERROR','WARNING'}]
    if other:sections.append('## Other review items\n\n'+'\n'.join('- '+text(f.get('message',f) if isinstance(f,dict) else f) for f in other))
    return '## Unresolved Items\n\n'+'\n\n'.join(sections)+'\n\nFull finding identities/evidence are retained in assessment.json and migration_plan.json.'


def compact_steps(plan,operations):
    """All-mode graph notebooks need no executable container-marker cells."""
    meta=plan.get('metadata',{})
    if meta.get('planner')!='source_graph' or not meta.get('requires_shared_runtime_state'):return set(),''
    groups={'Target audit replacements':[], 'Control and disabled sections':[]};skipped=set()
    for step in plan['steps']:
        sid=step['step_id'];ops=operations.get(sid,[])
        if step.get('metadata',{}).get('audit_replacement'):
            group='Target audit replacements'
        elif ops and all(op.get('operation_type')=='STRUCTURAL' for op in ops):group='Control and disabled sections'
        else:continue
        skipped.add(sid)
        groups[group].append(step)
    sections=[]
    for label,steps in groups.items():
        if not steps:continue
        sections.append(f'## {label} ({len(steps)})\n\n'+
            ('The target lifecycle replaces this source audit work. Original SQL/bindings remain in audit_replacement_plan.json.\n\n' if label.startswith('Target') else
             'Dependency ordering is preserved in the business cells. These structural/disabled sections have no separate data body in this shared notebook.\n\n')+
            '| Source | Type |\n| --- | --- |\n'+
            '\n'.join('| '+text(s.get('metadata',{}).get('source_ref_id',s['step_id']))+' | '+text(s['step_type'])+' |' for s in steps))
    if sections:sections.append('The complete task graph, step IDs, and dependencies remain in task_graph.json and migration_plan.json. These entries are excluded from business-operation coverage.')
    return skipped,'\n\n'.join(sections)
