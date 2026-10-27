"""Original procedure/KM command, option and text declarations without execution."""
from __future__ import annotations

from collections import defaultdict
import copy
import hashlib

from ..base import UnsupportedSourceError
from .native import numeric, value
from .expressions import _evidence

MAX_MODULES = 256
MAX_COMMANDS = 512
MAX_OPTIONS = 512
MAX_ASSOCIATIONS = 2048
MAX_TEXT_RECORDS = 2048
MAX_TEXT_CHARS = 65536


def command_requirements(index):
    modules = index.classes['SnpTrt']; commands = index.classes['SnpLineTrt']
    options = index.classes['SnpUserExit']; associations = index.classes['SnpLineTrtUe']
    texts = [*index.classes['SnpTxtHeader'], *index.classes['SnpMtxt']]
    if (len(modules) > MAX_MODULES or len(commands) > MAX_COMMANDS or len(options) > MAX_OPTIONS
            or len(associations) > MAX_ASSOCIATIONS or len(texts) > MAX_TEXT_RECORDS):
        raise UnsupportedSourceError('ODI procedure/KM declaration count limit exceeded.')
    module_index = defaultdict(list); command_index = defaultdict(list); line_index = defaultdict(list)
    option_index = defaultdict(list); global_options = defaultdict(list); text_index = defaultdict(list)
    for module in modules: module_index[value(module, 'ITrt')].append(module)
    for command in commands:
        command_index[(value(command, 'ITrt'), value(command, 'ILineTrt'))].append(command)
        line_index[value(command, 'ILineTrt')].append(command)
    for option in options:
        option_index[(value(option, 'ITrt'), value(option, 'IUserExit'))].append(option)
        global_options[value(option, 'IUserExit')].append(option)
    for text in texts: text_index[value(text, 'ITxt')].append(text)

    def text_binding(identity):
        matches = text_index.get(identity, []) if numeric(identity) else []
        records = []
        for node in matches:
            fields = {name: value(node, name) for name in ('Txt', 'FullTxt')}
            if any(isinstance(text, str) and len(text) > MAX_TEXT_CHARS for text in fields.values()):
                raise UnsupportedSourceError('ODI procedure/KM text declaration exceeds its character limit.')
            records.append({'node_id': node['node_id'], 'fields': fields,
                'text_sha256_utf8': {name: hashlib.sha256(text.encode()).hexdigest() if isinstance(text, str) else None for name, text in fields.items()},
                'encryption_declaration': value(node, 'Enc'),
                'contains_template_markers': any(isinstance(text, str) and any(marker in text for marker in ('<%', '%>', 'odiRef.')) for text in fields.values()),
                'evidence': _evidence([node], ('ITxt', 'Txt', 'FullTxt', 'Enc'))})
        return {'declared_text_identity': identity,
                'resolution': 'ONE_DECLARED_RECORD' if len(matches) == 1 else 'AMBIGUOUS' if matches else 'MISSING' if numeric(identity) else 'UNBOUND_DECLARATION',
                'records': records, 'effective_text': None, 'interpretation': 'DECLARED_TEXT_ONLY'}

    items = []; association_records = {}
    for association in associations:
        line_id = value(association, 'ILineTrt'); option_id = value(association, 'IUserExit'); module_id = value(association, 'ITrt')
        lines = line_index.get(line_id, []) if numeric(line_id) else []
        exits = global_options.get(option_id, []) if numeric(option_id) else []
        owners = module_index.get(module_id, []) if numeric(module_id) else []
        consistent = (len(lines) == len(exits) == len(owners) == 1
                      and value(lines[0], 'ITrt') == value(exits[0], 'ITrt') == module_id)
        declarations = {'declared_fields': copy.deepcopy(association['attributes']['declared_fields']),
            'declared_line_identity': line_id, 'declared_option_identity': option_id, 'declared_module_identity': module_id,
            'line_identity_candidate_node_ids': [n['node_id'] for n in lines],
            'option_identity_candidate_node_ids': [n['node_id'] for n in exits],
            'module_identity_candidate_node_ids': [n['node_id'] for n in owners],
            'line_module_declarations': [value(n, 'ITrt') for n in lines],
            'option_module_declarations': [value(n, 'ITrt') for n in exits],
            'resolution': 'BOUND_DECLARATIONS_ONLY' if consistent else 'UNRESOLVED', 'effective_binding': None}
        association_records[association['node_id']] = declarations
        items.append((association, 'ODI_PROCEDURE_KM_OPTION_ASSOCIATION', declarations,
            ['Association identities, module scope and option semantics require verification; differing declarations are not repaired or assigned runtime precedence.'],
            [*lines, *exits, *owners]))
    for module in modules:
        identity = value(module, 'ITrt')
        steps = [n for n in commands if value(n, 'ITrt') == identity]
        declared_options = [n for n in options if value(n, 'ITrt') == identity]
        ranks = defaultdict(list)
        for step in steps: ranks[value(step, 'OrdTrt')].append(step['node_id'])
        items.append((module, 'ODI_PROCEDURE_KM', {
            'declared_identity': identity, 'name': value(module, 'TrtName'), 'declared_type': value(module, 'TrtType'),
            'command_node_ids': [n['node_id'] for n in steps], 'option_node_ids': [n['node_id'] for n in declared_options],
            'declared_order_groups': [{'rank_text': rank, 'node_ids': nodes} for rank, nodes in ranks.items()],
            'execution_order_status': 'NOT_ESTABLISHED', 'effective_options': None},
            ['Procedure/KM type, condition/rank, inheritance, options, command expansion and runtime behavior remain unverified.'],
            [*steps, *declared_options]))
    for command in commands:
        identity = value(command, 'ITrt'); owners = module_index.get(identity, []) if numeric(identity) else []
        unique_command = len(command_index[(identity, value(command, 'ILineTrt'))]) == 1
        related = list(owners); bindings = []
        for association in associations:
            declaration = association_records[association['node_id']]
            if command['node_id'] not in declaration['line_identity_candidate_node_ids']: continue
            matches = option_index.get((identity, value(association, 'IUserExit')), [])
            bindings.append({'node_id': association['node_id'], 'declared_option_identity': value(association, 'IUserExit'),
                'declared_rank': value(association, 'OrdTrt'), 'option_node_id': matches[0]['node_id'] if len(matches) == 1 else None,
                'association_module_declaration': value(association, 'ITrt'),
                'resolution': 'BOUND' if declaration['resolution'] == 'BOUND_DECLARATIONS_ONLY' and len(matches) == 1 and unique_command else 'UNRESOLVED'})
            related.extend([association, *matches])
        sides = {}
        for side, prefix in [('definition', 'Def'), ('collection', 'Col')]:
            binding = text_binding(value(command, prefix+'ITxt'))
            sides[side] = {'technology_declaration': value(command, prefix+'Techno'),
                'language_declaration': value(command, prefix+'Language'), 'commit_declaration': value(command, prefix+'IndCommit'),
                'isolation_declaration': value(command, prefix+'IsolLevel'), 'text_binding': binding}
            related.extend(index.nodes[r['node_id']] for r in binding['records'])
        issues = ['Command language/technology, option conditions, templates, inheritance and error/commit flags are declarations; no effective command or execution order is established.']
        if len(owners) != 1: issues.append('Command owner definition is missing or ambiguous; no owner was chosen.')
        if not unique_command: issues.append('Command identity is ambiguous within its module; option associations do not establish an execution owner.')
        if any(s['text_binding']['resolution'] in ('MISSING', 'AMBIGUOUS') for s in sides.values()):
            issues.append('A declared command text identity is missing or ambiguous; no replacement text was fabricated.')
        if any(b['resolution'] != 'BOUND' for b in bindings): issues.append('Command option associations do not establish a unique option/module binding; original declarations are retained.')
        items.append((command, 'ODI_PROCEDURE_KM_COMMAND', {
            'declared_identity': value(command, 'ILineTrt'), 'owner_node_id': owners[0]['node_id'] if len(owners) == 1 else None,
            'name': value(command, 'SqlName'), 'declared_fields': copy.deepcopy(command['attributes']['declared_fields']),
            'sides': sides, 'option_associations': bindings, 'effective_command': None, 'execution_status': 'NOT_RUN'}, issues, related))
    for option in options:
        identity = value(option, 'ITrt'); owners = module_index.get(identity, []) if numeric(identity) else []
        defaults = text_binding(value(option, 'ITxtValue')); expression = text_binding(value(option, 'ExprITxt'))
        related = [*owners, *[index.nodes[r['node_id']] for b in (defaults, expression) for r in b['records']]]
        items.append((option, 'ODI_PROCEDURE_KM_OPTION', {
            'declared_identity': value(option, 'IUserExit'), 'name': value(option, 'UeName'),
            'owner_node_id': owners[0]['node_id'] if len(owners) == 1 else None,
            'declared_fields': copy.deepcopy(option['attributes']['declared_fields']),
            'default_text_binding': defaults, 'expression_text_binding': expression, 'effective_value': None},
            ['Option defaults, types, precedence, inheritance and effective runtime values remain unverified.'], related))
    return items
