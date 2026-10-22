"""Contract-bound Python candidates for the SSIS_PURE_MAIN_V1 capability.

Candidates define a driver-side function and never call it automatically.
Package control flow, connections and job wiring still require separate review.
"""
from __future__ import annotations

import ast
import hashlib

from ssis_migration.agent.code_validator import validate_generated_code
from ssis_migration.adapters.ssis.csharp_lowering import UnsupportedCSharp, lower_main
from ssis_migration.migration import validate_contract
from ssis_migration.schemas import SchemaValidationError, validate_document
from .models import GeneratedArtifact


_HEADER = '''# REVIEW_REQUIRED: Bounded C# candidate; verify bindings and package orchestration.
def run_ssis_script(variables):
    """Mutate explicitly bound SSIS variables and return the supplied success code."""
    def _i32(value):
        return (value + 2147483648) % 4294967296 - 2147483648
'''


def _expression(node):
    op = node['op']
    if op == 'literal':
        return repr(node['value'])
    if op == 'local':
        return node['name']
    if op == 'variable':
        return 'variables[' + repr(node['name']) + ']'
    if op == 'unary':
        value = _expression(node['value'])
        if node['operator'] == '!':
            return '(not ' + value + ')'
        result = '(' + node['operator'] + value + ')'
        return '_i32(' + result + ')'
    if op == 'binary':
        operator = {'&&': 'and', '||': 'or'}.get(node['operator'], node['operator'])
        result = '(' + _expression(node['left']) + ' ' + operator + ' ' + _expression(node['right']) + ')'
        return '_i32(' + result + ')' if node['type'] == 'int32' else result
    raise ValueError('Unexpected typed expression: ' + op)


def _statements(node, indent=1):
    prefix = '    ' * indent
    op = node['op']
    if op == 'block':
        return ''.join(_statements(child, indent) for child in node['statements']) or prefix + 'pass\n'
    if op in {'assign_local', 'assign_variable'}:
        target = node['name'] if op == 'assign_local' else 'variables[' + repr(node['name']) + ']'
        return prefix + target + ' = ' + _expression(node['value']) + '\n'
    if op == 'task_result':
        return prefix + 'return ' + _expression(node['value']) + '\n'
    if op == 'if':
        result = prefix + 'if ' + _expression(node['condition']) + ':\n' + _statements(node['then'], indent + 1)
        if node['otherwise'] is not None:
            result += prefix + 'else:\n' + _statements(node['otherwise'], indent + 1)
        return result
    raise ValueError('Unexpected typed statement: ' + op)


def _validate_spec(contract, spec):
    validate_document(spec, 'csharp_translation_spec')
    available = {item['file_id'] for item in contract['evidence_files'] if item['status'] == 'AVAILABLE'}
    for record in [spec['runtime'], *spec['variables'].values()]:
        for ref in record['evidence']:
            if ref['file_id'] not in available:
                raise UnsupportedCSharp('Runtime/variable binding cites unavailable or unknown evidence.')


def generate_csharp_candidate(contract: dict) -> GeneratedArtifact:
    """Validate the contract first, then reparse/re-lower source before emitting.

    No persisted assessment/AST is trusted as executable code. Missing evidence
    or any unsupported source prevents code emission; the contract is unchanged.
    """
    validate_contract(contract)
    metadata = contract['source_behavior']['legacy_step'].get('metadata', {})
    step_id = contract['step_id']
    common = {'migration_contract_id': contract['contract_id'], 'semantic_status': 'NOT_VERIFIED',
              'execution_status': 'NOT_RUN', 'data_equivalence_status': 'NOT_RUN',
              'source_compilation_status': 'NOT_RUN',
              'coverage_scope': 'SELECTED_SOURCE_FILE_SUBSET',
              'assumptions': ['Supplied binding evidence correctly resolves the SSIS runtime, variable types and unchecked compilation context.',
                              'String inputs are non-null Unicode scalar strings; int inputs are exactly signed int32.',
                              'The function caller must supply SSIS state and wire success/failure into the reviewed Databricks job.'],
              'validation_requirements': ['Compile the exact source against supplied real reference assemblies and recorded options.',
                                          'Verify supplied runtime and variable-binding evidence.',
                                          'Run independent source/target semantic and reconciliation tests.',
                                          'Review all other contract obligations and job control flow.']}
    try:
        if contract['source_platform'] != 'ssis' or contract['target_platform'] != 'databricks':
            raise UnsupportedCSharp('Only SSIS ScriptTask to Databricks candidates are supported.')
        if contract['implementation_strategy']['decision'] != 'DETERMINISTIC':
            raise UnsupportedCSharp('An explicit DETERMINISTIC contract strategy is required; no strategy is promoted.')
        spec = metadata.get('csharp_translation')
        _validate_spec(contract, spec)
        scope = metadata.get('normalized_source', {})
        candidates = [wrapper for wrapper in scope.get('nodes', []) if wrapper['record']['node_id'] == spec['source_node_id']]
        if len(candidates) != 1 or candidates[0]['obligation_scope'] != 'STEP' or scope.get('root_node_id') != spec['source_node_id']:
            raise UnsupportedCSharp('C# candidate must identify a uniquely owned source root.')
        node = candidates[0]['record']
        if node['kind'] != 'TASK':
            raise UnsupportedCSharp('ScriptComponent row/buffer semantics are outside this capability.')
        assessment = node['attributes'].get('script_assessment', {})
        if assessment.get('identity_status') != 'RESOLVED' or len(assessment.get('files', [])) != 1:
            raise UnsupportedCSharp('Exactly one identified C# source file is required; project composition remains unresolved.')
        source_assessment = assessment['files'][0].get('assessment')
        validate_document(source_assessment, 'csharp_assessment')
        source = source_assessment['source']
        if source['file_id'] not in {f['file_id'] for f in contract['evidence_files'] if f['status'] == 'AVAILABLE'}:
            raise UnsupportedCSharp('C# source evidence is unavailable.')
        actual_digest = hashlib.sha256(source['text'].encode('utf-8')).hexdigest()
        if source['locator'] != spec['source_locator'] or actual_digest != spec['source_sha256'] or actual_digest != source['sha256']:
            raise UnsupportedCSharp('C# source selector/hash does not match the contract-bound source.')
        program = lower_main(source['text'], spec, file_id=source['file_id'], locator=source['locator'])
        code = _HEADER
        for name, binding in program['variables'].items():
            test = {'int32': "type(_value) is int and -2147483648 <= _value <= 2147483647",
                    'bool': 'type(_value) is bool',
                    'string': 'type(_value) is str and not any(55296 <= ord(_c) <= 57343 for _c in _value)'}[binding['type']]
            code += '    _value = variables[' + repr(name) + ']\n'
            code += '    if not (' + test + '):\n        raise TypeError(' + repr('Invalid supplied SSIS variable: ' + name) + ')\n'
        code += _statements(program['program'])
        # Independent target syntax and existing static-validator boundary.
        compile(ast.parse(code), '<csharp_candidate>', 'exec')
        validation = validate_generated_code(code, language='pyspark')
        if validation['status'] == 'REJECTED':
            raise UnsupportedCSharp('Generated candidate failed independent target validation.')
        return GeneratedArtifact(artifact_id=step_id + '_csharp_candidate', name=step_id + '_csharp_candidate',
                                 artifact_type='CODE', target_platform='databricks', language='pyspark',
                                 code=code, source_step_id=step_id,
                                 metadata={**common, 'status': 'REVIEW_REQUIRED', 'capability': program['capability'],
                                           'source_sha256': actual_digest, 'lowered_program': program,
                                           'static_validation': validation})
    except (UnsupportedCSharp, SchemaValidationError, RecursionError) as exc:
        return GeneratedArtifact(artifact_id=step_id + '_csharp_unresolved', name=step_id + '_csharp_unresolved',
                                 artifact_type='UNRESOLVED', target_platform='databricks', language='pyspark',
                                 code='', source_step_id=step_id,
                                 metadata={**common, 'status': 'UNSUPPORTED', 'unresolved_items': [str(exc)],
                                           'source_span': {'start_byte': getattr(exc, 'start_byte', 0),
                                                           'end_byte': getattr(exc, 'end_byte', 0)}})
