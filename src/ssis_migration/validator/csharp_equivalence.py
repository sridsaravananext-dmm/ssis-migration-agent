"""Offline comparison of explicitly observed, contract-bound C# scalar cases.

This module does not execute source, candidate functions or target workloads.
A match proves agreement only for the supplied successful cases and runtime
scope. Observation files are operator attestations, not authenticated records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ssis_migration.generator.csharp import generate_csharp_candidate
from ssis_migration.migration import validate_contract
from ssis_migration.schemas import validate_document


def document_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _typed(value, dtype):
    if dtype == 'int32':
        return type(value) is int and -2147483648 <= value <= 2147483647
    if dtype == 'bool':
        return type(value) is bool
    return type(value) is str and not any(0xD800 <= ord(char) <= 0xDFFF for char in value)


def compare_csharp_traces(contract, compilation, source_trace, target_trace, *, compilation_root=None):
    """Compare complete scalar states and success results with strict types."""
    validate_contract(contract)
    validate_document(compilation, 'csharp_compilation_report')
    validate_document(source_trace, 'csharp_execution_trace')
    validate_document(target_trace, 'csharp_execution_trace')
    candidate = generate_csharp_candidate(contract)
    candidate_hash = hashlib.sha256(candidate.code.encode()).hexdigest()
    result = {'equivalence_version': '1.0', 'status': 'BLOCKED', 'scope': 'OBSERVED_CASES_ONLY',
              'runtime_scope': compilation['scope'], 'contract_id': contract['contract_id'],
              'candidate_sha256': candidate_hash, 'compilation_report_sha256': document_digest(compilation),
              'source_trace_sha256': document_digest(source_trace), 'target_trace_sha256': document_digest(target_trace),
              'matched_case_count': 0, 'case_count': 0, 'case_results': [], 'diagnostics': [],
              'compilation_artifacts_verified': False,
              'migration_accepted': False, 'weighted_semantic_coverage': None,
              'limitations': ['Only supplied successful input cases are compared; no general equivalence is inferred.',
                              'Fixture stubs do not establish actual SSIS runtime behavior.',
                              'Python driver execution does not establish Databricks job, Spark or data equivalence.',
                              'Observation files and runtime labels are operator attestations, not authenticated execution.',
                              'Weighted coverage, critical rules and all existing acceptance gates still require review.']}
    problems = result['diagnostics']
    if compilation_root is not None:
        from ssis_migration.languages.csharp.compiler import verify_compilation_artifacts
        try:
            verify_compilation_artifacts(compilation, compilation_root)
            result['compilation_artifacts_verified'] = True
        except (OSError, ValueError) as exc:
            problems.append('Compilation artifacts could not be verified: ' + str(exc))

    if candidate.artifact_type != 'CODE':
        problems.append('The current contract has no supported deterministic C# candidate.')
    if compilation['options']['check_overflow']:
        problems.append('Compilation overflow context conflicts with the explicit unchecked candidate binding.')
    if compilation['status'] != 'PASS':
        problems.append('Source compilation did not pass.')
    if candidate.artifact_type == 'CODE':
        expected_source = candidate.metadata['source_sha256']
        if expected_source not in {record['sha256'] for record in compilation['sources']}:
            problems.append('Compiled sources do not include the exact contract-bound C# source.')
        for trace, side in ((source_trace, 'SOURCE'), (target_trace, 'TARGET')):
            for key, expected in [('side', side), ('contract_id', contract['contract_id']),
                                  ('source_sha256', expected_source), ('candidate_sha256', candidate_hash),
                                  ('compilation_report_sha256', document_digest(compilation))]:
                if trace[key] != expected:
                    problems.append(side + ' trace has a stale or conflicting ' + key + '.')
        if source_trace['runtime_scope'] != compilation['scope']:
            problems.append('Source trace runtime scope conflicts with compilation scope.')
        if target_trace['runtime_scope'] != 'PYTHON_DRIVER':
            problems.append('This comparator only supports explicit Python driver target observations.')
    source_cases = {case['case_id']: case for case in source_trace['cases']}
    target_cases = {case['case_id']: case for case in target_trace['cases']}
    if len(source_cases) != len(source_trace['cases']) or len(target_cases) != len(target_trace['cases']):
        problems.append('Duplicate case identities cannot establish evidence coverage.')
    if set(source_cases) != set(target_cases):
        problems.append('Source and target case sets differ; missing cases are not excluded.')
    if not problems:
        program = candidate.metadata['lowered_program']
        bindings = program['variables']
        for case_id in sorted(source_cases):
            source, target = source_cases[case_id], target_cases[case_id]
            issues = []
            if document_digest(source['input_variables']) != document_digest(target['input_variables']):
                issues.append('Input states/types differ.')
            for case in (source, target):
                inputs, outcome = case['input_variables'], case['outcome']
                if any(key not in inputs or not _typed(inputs[key], binding['type']) for key, binding in bindings.items()):
                    issues.append('Input state violates supplied variable bindings.')
                if outcome['status'] != 'RETURNED' or outcome['exception'] is not None:
                    issues.append('A successful source and target return is required; exceptions do not earn coverage.')
                if type(outcome['task_result']) is not int or outcome['task_result'] != program['runtime']['success_value']:
                    issues.append('Return does not match the supplied success binding.')
                if set(outcome['variables']) != set(inputs):
                    issues.append('Post-state omits or invents variables.')
                if any(key not in outcome['variables'] or not _typed(outcome['variables'][key], binding['type'])
                       for key, binding in bindings.items()):
                    issues.append('Output state violates supplied variable types.')
                for key, binding in bindings.items():
                    if binding['access'] == 'READ' and key in inputs and key in outcome['variables']:
                        if document_digest(inputs[key]) != document_digest(outcome['variables'][key]):
                            issues.append('Read-only variable changed: ' + key)
            if document_digest(source['outcome']) != document_digest(target['outcome']):
                issues.append('Complete outcomes or value types differ.')
            matched = not issues
            result['case_results'].append({'case_id': case_id, 'status': 'MATCH' if matched else 'MISMATCH',
                                           'issues': sorted(set(issues))})
            result['matched_case_count'] += int(matched)
        result['case_count'] = len(source_cases)
        result['status'] = 'MATCH' if result['matched_case_count'] == result['case_count'] else 'MISMATCH'
    validate_document(result, 'csharp_equivalence_report')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='Compare observed C# and Python fixture traces offline; never executes workloads.')
    for name in ('contract', 'compilation', 'source_trace', 'target_trace', 'output'):
        parser.add_argument('--' + name.replace('_', '-'), required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        def read(path):
            return json.loads(path.read_text())
        result = compare_csharp_traces(read(args.contract), read(args.compilation), read(args.source_trace), read(args.target_trace),
                                       compilation_root=args.compilation.parent)
        with args.output.open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')
    print(json.dumps({'status': result['status'], 'scope': result['scope'], 'matched_cases': result['matched_case_count']}))
    return 0 if result['status'] == 'MATCH' else 1


if __name__ == '__main__':
    raise SystemExit(main())
