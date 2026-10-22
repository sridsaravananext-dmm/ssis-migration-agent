"""Nominal intra-section TI flow; no inter-section row-loop assumptions."""
from __future__ import annotations

from ssis_migration.ir.normalized import content_digest


def statement_id(section,node):
    return 'ti-statement-'+content_digest([section,node['kind'],node['span']])[:24]


def flatten(section,statements):
    result=[]
    def visit(nodes,parent=None,branch=None):
        for node in nodes:
            identity=statement_id(section,node)
            result.append({'statement_id':identity,'section':section,'source_order':len(result),'kind':node['kind'],
                           'span':node['span'],'parent_id':parent,'branch':branch})
            if node['kind']=='if':
                for i,part in enumerate(node['branches']):visit(part['body'],identity,'branch:'+str(i))
            elif node['kind']=='while':visit(node['body'],identity,'loop_body')
    visit(statements)
    return result


def build_control_flow(section,statements):
    """Retain guarded branches/back-edges and unresolved early-control targets."""
    entry='ti-entry-'+section;exit_id='ti-exit-'+section;edges=[]
    def add(start,end,kind,span,guards=None,control_name=None):
        value={'source_id':start,'target_id':end,'kind':kind,'span':span,'guards':guards or [],
               'control_name':control_name,'resolution':'UNRESOLVED_CONTROL_TARGET' if end is None else 'STRUCTURAL'}
        value['edge_id']='ti-edge-'+content_digest([section,value])[:24];edges.append(value)
    def sequence(nodes,successor,tail_kind='SEQUENCE'):
        if not nodes:return successor
        for i,node in enumerate(nodes):
            identity=statement_id(section,node)
            next_id=statement_id(section,nodes[i+1]) if i+1<len(nodes) else successor
            kind=tail_kind if i+1==len(nodes) else 'SEQUENCE'
            if node['kind']=='if':
                preceding=[]
                for part in node['branches']:
                    guards=preceding.copy()
                    if part['condition'] is not None:guards.append({'span':part['condition']['span'],'expected':True})
                    target=sequence(part['body'],next_id,kind)
                    add(identity,target,'BRANCH',node['span'],guards)
                    if part['condition'] is not None:preceding.append({'span':part['condition']['span'],'expected':False})
                if node['branches'][-1]['condition'] is not None:add(identity,next_id,'BRANCH',node['span'],preceding)
            elif node['kind']=='while':
                target=sequence(node['body'],identity,'LOOP_BACK')
                add(identity,target,'LOOP_ENTER',node['span'],[{'span':node['condition']['span'],'expected':True}])
                add(identity,next_id,'LOOP_EXIT',node['span'],[{'span':node['condition']['span'],'expected':False}])
            elif node['kind']=='control' or (node['kind']=='invoke' and node['call']['name'].upper()=='ITEMREJECT'):
                control=node['name'] if node['kind']=='control' else 'ITEMREJECT'
                add(identity,None,'CONTROL_TRANSFER',node['span'],control_name=control)
            else:add(identity,next_id,kind,node['span'])
        return statement_id(section,nodes[0])
    first=sequence(statements,exit_id)
    add(entry,first,'SECTION_ENTRY',{'start':0,'end':0})
    return {'section':section,'entry_id':entry,'exit_id':exit_id,'scope':'NOMINAL_INTRA_SECTION_ONLY','edges':edges}
