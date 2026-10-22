"""Explicit offline Roslyn compilation of supplied source/reference files.

No project files, build targets, source generators, package restore or source
execution are used. A compilation PASS is scoped to the supplied references;
it never establishes real SSIS runtime binding or migration equivalence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from ssis_migration.schemas import validate_document


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _environment():
    env = {key: os.environ[key] for key in ('PATH', 'SystemRoot', 'WINDIR', 'TMP', 'TEMP', 'TMPDIR', 'LANG') if key in os.environ}
    env.update(DOTNET_NOLOGO='1', DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1',
               DOTNET_MULTILEVEL_LOOKUP='0')
    return env


def _input(record, suffix):
    path = Path(record['path']).expanduser()
    if not path.is_absolute():
        raise ValueError('Compiler input paths must be absolute.')
    if path.is_symlink() or not path.is_file() or path.suffix.lower() != suffix:
        raise ValueError('Expected a regular ' + suffix + ' input: ' + str(path))
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Compiler input exceeds the explicit 32 MiB per-file limit: ' + str(path))
    data = path.read_bytes()
    if _digest(data) != record['sha256']:
        raise ValueError('Compiler input no longer matches its supplied SHA-256: ' + str(path))
    return path, data


def compile_csharp(request: dict, output_dir: str | Path, *, dotnet: str | Path = 'dotnet', timeout_seconds: int = 60) -> dict:
    """Compile only when explicitly called; write a new report directory.

    Request paths must be absolute and hashes must describe actual source and
    reference bytes. The caller supplies the SDK, language and overflow context.
    Missing tooling/refs are reported rather than replaced by guessed stubs.
    """
    validate_document(request, 'csharp_compilation_request')
    if not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 120:
        raise ValueError('Compiler timeout must be between 1 and 120 seconds.')
    output = Path(output_dir).expanduser().absolute()
    output.mkdir(parents=True, exist_ok=False)
    (output/'request.json').write_bytes(_canonical(request))
    result = {'compilation_version': '1.0', 'status': 'BLOCKED', 'scope': request['scope'],
              'request_sha256': _digest(_canonical(request)), 'toolchain': None,
              'options': {key: request[key] for key in ('language_version', 'check_overflow', 'output_kind')},
              'sources': [], 'references': [], 'diagnostics': [], 'exit_code': None,
              'artifacts': {}, 'source_execution': 'NOT_RUN', 'semantic_status': 'NOT_VERIFIED',
              'runtime_binding_status': 'NOT_VERIFIED', 'migration_accepted': False,
              'limitations': ['Compilation is scoped to the supplied reference assemblies and options.',
                              'No SSIS runtime, generated candidate, Databricks workload or package acceptance ran.']}
    try:
        copied = {'sources': [], 'references': []}
        originals = []
        for collection, suffix in (('sources', '.cs'), ('references', '.dll')):
            for i, record in enumerate(request[collection]):
                path, data = _input(record, suffix)
                destination = output/(collection + '-' + str(i) + suffix)
                destination.write_bytes(data)
                originals.append((record, suffix))
                copied[collection].append(destination)
                result[collection].append({'path': str(path), 'sha256': record['sha256'],
                                           'snapshot_path': destination.name})
        executable = shutil.which(str(dotnet))
        if executable is None:
            result['diagnostics'].append('Requested dotnet executable is unavailable; no compilation attempted.')
        else:
            sdk_list = subprocess.run([executable, '--list-sdks'], cwd=output, env=_environment(),
                                      capture_output=True, text=True, timeout=timeout_seconds, check=False)
            if sdk_list.returncode:
                raise ValueError('dotnet SDK inventory failed: ' + sdk_list.stderr[:4000])
            found = []
            for line in sdk_list.stdout.splitlines():
                match = re.fullmatch(r'(\S+) \[(.+)\]', line.strip())
                if match and match[1] == request['sdk_version']:
                    found.append(Path(match[2])/match[1]/'Roslyn'/'bincore'/'csc.dll')
            if len(found) != 1 or not found[0].is_file():
                raise ValueError('Requested SDK version is not uniquely available: ' + request['sdk_version'])
            compiler = found[0]
            result['toolchain'] = {'sdk_version': request['sdk_version'], 'dotnet': str(Path(executable).resolve()),
                                   'compiler_sha256': _digest(compiler.read_bytes())}
            assembly = output/'compiled.dll'
            command = [executable, str(compiler), '/nologo', '/noconfig', '/nostdlib+',
                       '/target:' + ('library' if request['output_kind'] == 'LIBRARY' else 'exe'),
                       '/langversion:' + request['language_version'], '/deterministic+', '/unsafe-',
                       '/checked' + ('+' if request['check_overflow'] else '-'), '/out:' + str(assembly)]
            if request['output_kind'] == 'CONSOLE':
                command.append('/main:' + request['main_type'])
            command.extend('/reference:' + str(path) for path in copied['references'])
            command.extend(str(path) for path in copied['sources'])
            (output/'command.json').write_bytes(_canonical(command))
            log = output/'compiler.log'
            with log.open('w') as stream:
                run = subprocess.run(command, cwd=output, env=_environment(), stdout=stream, stderr=subprocess.STDOUT,
                                     timeout=timeout_seconds, check=False)
            result['exit_code'] = run.returncode
            result['artifacts']['compiler_log'] = {'path': log.name, 'sha256': _digest(log.read_bytes())}
            result['diagnostics'].append(log.read_text(errors='replace')[:16000])
            # Recheck every contributing original after compilation, not just snapshots.
            for record, suffix in originals:
                _input(record, suffix)
            if run.returncode == 0 and assembly.is_file():
                result['status'] = 'PASS'
                result['artifacts']['assembly'] = {'path': assembly.name, 'sha256': _digest(assembly.read_bytes())}
            else:
                result['status'] = 'FAIL'
    except subprocess.TimeoutExpired:
        result['status'] = 'TIMEOUT'
        result['diagnostics'].append('Explicit compiler timeout reached; no result is treated as passing.')
    except (ValueError, OSError) as exc:
        result['diagnostics'].append(str(exc))
    validate_document(result, 'csharp_compilation_report')
    (output/'compilation_report.json').write_text(json.dumps(result, indent=2) + '\n')
    return result



def verify_compilation_artifacts(report: dict, report_root: str | Path) -> None:
    """Verify stored request, source/reference snapshots, log and assembly hashes.

    This detects changed/omitted artifacts, not fabricated operator attestations.
    It does not execute or load any assembly.
    """
    validate_document(report, 'csharp_compilation_report')
    if report['status'] != 'PASS':
        raise ValueError('Only a passing compilation can supply verified artifacts.')
    root = Path(report_root).resolve(strict=True)
    def inside(name):
        original = root/name
        resolved = original.resolve(strict=True)
        if original.is_symlink() or not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError('Compilation artifact is missing, external or a symlink: ' + name)
        return resolved
    request = json.loads(inside('request.json').read_text())
    validate_document(request, 'csharp_compilation_request')
    if _digest(_canonical(request)) != report['request_sha256'] or request['scope'] != report['scope']:
        raise ValueError('Compilation request no longer matches the report.')
    if report['options'] != {key: request[key] for key in ('language_version', 'check_overflow', 'output_kind')}:
        raise ValueError('Compilation options no longer match the recorded request.')
    if report['toolchain']['sdk_version'] != request['sdk_version']:
        raise ValueError('Compilation SDK no longer matches the recorded request.')
    for collection in ('sources', 'references'):
        if len(request[collection]) != len(report[collection]):
            raise ValueError('Compilation report omitted requested input records.')
        for expected, recorded in zip(request[collection], report[collection]):
            if (expected['sha256'] != recorded['sha256'] or
                    str(Path(expected['path']).expanduser()) != recorded['path'] or
                    _digest(inside(recorded['snapshot_path']).read_bytes()) != recorded['sha256']):
                raise ValueError('Compilation source/reference snapshot no longer matches its request.')
    for artifact in report['artifacts'].values():
        if _digest(inside(artifact['path']).read_bytes()) != artifact['sha256']:
            raise ValueError('Compilation output artifact hash no longer matches: ' + artifact['path'])


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline C# compilation against explicitly supplied references; never runs source.')
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--dotnet', default='dotnet')
    args = parser.parse_args(argv)
    try:
        request = json.loads(args.request.read_text())
        result = compile_csharp(request, args.output, dotnet=args.dotnet)
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')
    print(json.dumps({'status': result['status'], 'scope': result['scope'], 'report': str(args.output/'compilation_report.json')}))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
