"""Non-executing policy checks used on every emitted Python/SQL artifact."""
from __future__ import annotations
import ast
import re
from ssis_migration.sql_text import mask_sql

UDF_NAMES = {'udf', 'pandas_udf', 'udtf', 'registerJavaFunction', 'registerJavaUDAF',
             'UserDefinedFunction', 'UserDefinedTableFunction', 'mapInPandas', 'applyInPandas', 'applyInPandasWithState', 'mapInArrow'}


def validate_native_spark(code: str, *, language='python'):
    issues = []
    def issue(code, message, line=None):
        item = {'code': code, 'severity': 'ERROR', 'message': message, 'line': line}
        if item not in issues: issues.append(item)
    def sql_checks(sql, line=None):
        clean = mask_sql(sql)
        if re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP(?:ORARY)?\s+)?FUNCTION\b', clean, re.I):
            issue('UDF_POLICY_VIOLATION', 'Generate native Spark expressions instead of defining a function.', line)
        if re.search(r'(?<!:):[A-Za-z_]\w*|@[A-Za-z_]\w*|\?', clean):
            issue('RUNTIME_PARAMETER_UNBOUND', 'SQL contains unresolved runtime parameter tokens.', line)
        if re.search(r'\b(?:EXEC(?:UTE)?|sp_executesql)\b', clean, re.I):
            issue('LEGACY_SQL_EXECUTION', 'Legacy procedure execution must be translated, not passed to Spark.', line)
    if language in {'spark_sql', 'sql'}:
        if not mask_sql(code).strip() or re.search(r'\b(?:AI_REQUIRED|REVIEW_REQUIRED|TODO|placeholder)\b', code, re.I):
            issue('INCOMPLETE_IMPLEMENTATION', 'No complete SQL implementation was generated.')
        sql_checks(code)
        return {'status': 'REJECTED' if issues else 'PASS', 'issues': issues}
    try:
        tree = ast.parse(code)
        compile(tree, '<generated>', 'exec')
    except (SyntaxError, ValueError, TypeError) as exc:
        issue('PYTHON_SYNTAX', str(exc), getattr(exc, 'lineno', None))
        return {'status': 'REJECTED', 'issues': issues}
    aliases = {}
    sql_values = {}
    def sql_value(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str): return node.value
        if isinstance(node, ast.JoinedStr):
            return ''.join(x.value if isinstance(x, ast.Constant) else 'runtime_value' for x in node.values)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = sql_value(node.left), sql_value(node.right)
            if left is not None and right is not None: return left + right
        if isinstance(node, ast.Name): return sql_values.get(node.id)
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            value = sql_value(node.value)
            if value is not None:
                for target in node.targets:
                    if isinstance(target, ast.Name): sql_values[target.id] = value
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                aliases[alias.asname or alias.name] = f'{node.module}.{alias.name}'
                if node.module and node.module.startswith('pyspark') and (alias.name in UDF_NAMES or alias.name == '*'):
                    issue('UDF_POLICY_VIOLATION', 'Explicit native Spark imports are required; UDF APIs/wildcards are prohibited.', node.lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names: aliases[alias.asname or alias.name.split('.')[0]] = alias.name
    def name(n):
        if isinstance(n, ast.Name): return aliases.get(n.id, n.id)
        if isinstance(n, ast.Attribute): return name(n.value) + '.' + n.attr
        return ''
    # Propagate simple API aliases, including make_udf = F.udf.
    for _ in range(3):
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign):
                value = name(n.value)
                if value:
                    for target in n.targets:
                        if isinstance(target, ast.Name): aliases[target.id] = value
    for node in ast.walk(tree):
        if isinstance(node, (ast.Call, ast.Attribute, ast.Name)):
            target = name(node.func) if isinstance(node, ast.Call) else name(node)
            if target.split('.')[-1] in UDF_NAMES or '.udf.register' in target or '.udtf.register' in target:
                issue('UDF_POLICY_VIOLATION', 'Spark UDF/custom row APIs are prohibited; use native expressions.', getattr(node, 'lineno', None))
        if isinstance(node, ast.Call):
            target = name(node.func)
            if target == 'getattr' and len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and node.args[1].value in UDF_NAMES:
                issue('UDF_POLICY_VIOLATION', 'Dynamic access to UDF APIs is prohibited.', node.lineno)
            if target in {'exec', 'eval'}:
                issue('DYNAMIC_CODE_EXECUTION', 'Generated code must not execute dynamically constructed Python.', node.lineno)
            if target.endswith('.toPandas') or target.endswith('.collect'):
                # Bounded single-row SQL retrieval is permitted; bulk collections are not.
                receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
                bounded = (isinstance(receiver, ast.Call) and name(receiver.func).endswith('.limit')
                           and receiver.args and isinstance(receiver.args[0], ast.Constant)
                           and receiver.args[0].value == 1)
                if isinstance(receiver, ast.Call) and name(receiver.func).endswith('.sql') and receiver.args:
                    arg = receiver.args[0]
                    value = sql_value(arg)
                    if value is not None:
                        bounded = bool(re.search(r'\bLIMIT\s+1\s*;?\s*$', mask_sql(value), re.I))
                if not bounded:
                    issue('UNBOUNDED_DRIVER_COLLECTION', 'Bulk driver collection requires a verified bound; use distributed operations.', node.lineno)
            if target.endswith('.sql') and node.args:
                arg = node.args[0]
                value = sql_value(arg)
                if value is not None: sql_checks(value, node.lineno)
                else: issue('SQL_TEXT_NOT_STATIC', 'SQL source cannot be inspected statically; verify it before execution.', node.lineno)
    if re.search(r'\b(?:AI_REQUIRED|REVIEW_REQUIRED|TODO|placeholder|NotImplementedError)\b', code, re.I):
        issue('INCOMPLETE_IMPLEMENTATION', 'Generated content contains unresolved implementation markers.')
    if not tree.body or all(isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) for n in tree.body):
        issue('INCOMPLETE_IMPLEMENTATION', 'No executable implementation was generated.')
    return {'status': 'REJECTED' if issues else 'PASS', 'issues': issues}
