"""Bounded TI statement grammar reusing TM1 scalar-expression mechanics.

MDX/rule area references and rule controls are rejected, not reused as TI.
Statements and expressions are never evaluated here.
"""
from __future__ import annotations

from ..syntax import TM1SyntaxError, tokenize_tm1
from ..rules.parser import Parser as ScalarMechanics

SECTIONS = ('prolog','metadata','data','epilog')
_CONTROLS = {'ITEMSKIP','PROCESSQUIT','PROCESSERROR','PROCESSBREAK'}
_ARITY = {'ABS':{1},'ROUND':{2},'NUMBR':{1},'STR':{3},'TRIM':{1},'SCAN':{2},'SUBST':{3},'LONG':{1},
          'NUMBERTOSTRING':{1},'STRINGTONUMBER':{1},'CUBEEXISTS':{1},'DIMENSIONEXISTS':{1},
          'ODBCOPEN':{3},'ODBCOUTPUT':{2},'ODBCCLOSE':{1},'EXECUTECOMMAND':{2},
          'VIEWZEROOUT':{2},'CUBECLEARDATA':{1},'CUBEDESTROY':{1},'DIMENSIONDESTROY':{1},
          'SUBSETDESTROY':{2},'DIMENSIONELEMENTINSERT':{4},'ITEMREJECT':{1},
          'VIEWCREATE':{2,3},'SUBSETCREATE':{2,3}}


class Parser(ScalarMechanics):
    def __init__(self, source):
        self.tokens=tokenize_tm1(source,label='TurboIntegrator')
        self.index=0;self.depth=0;self.heights={};self.findings=[]
        self.block_depth=0

    def atom(self):
        token=self.token
        if token.kind in {'[','!'}:
            raise TM1SyntaxError('UNSUPPORTED_TI_EXPRESSION','Rule areas/dimension-context syntax are not TI variables.',token.start,token.end,'UNSUPPORTED')
        if token.kind=='NAME':
            name=self.take()
            if self.token.kind!='(':
                return self.node('variable',name.start,name.end,name=name.value)
            self.take();arguments=[]
            if self.token.kind!=')':
                while True:
                    arguments.append(self.expression())
                    if self.token.kind!=',':break
                    self.take()
            finish=self.expect(')')
            node=self.node('call',name.start,finish.end,name=name.value,arguments=arguments)
            upper=name.value.upper();count=len(arguments)
            if upper in {'CELLPUTN','CELLPUTS','CELLINCREMENTN'}:valid=count>=3
            elif upper in {'CELLGETN','CELLGETS'}:valid=count>=2
            elif upper in {'EXECUTEPROCESS','RUNPROCESS'}:valid=count>=1 and count%2==1
            elif upper in {'ASCIIOUTPUT','TEXTOUTPUT'}:valid=count>=2
            else:valid=count in _ARITY.get(upper,set())
            if not valid:self.note('UNSUPPORTED_FUNCTION_SIGNATURE','TI function/signature requires further analysis: '+name.value,node)
            return node
        return super().atom()

    def is_name(self,*names):return self.token.kind=='NAME' and self.token.value.upper() in names

    def block(self, terminators=()):
        self.block_depth+=1
        if self.block_depth>16:
            raise TM1SyntaxError('BLOCK_DEPTH_LIMIT','TI block nesting exceeded; no prefix was accepted.',self.token.start,self.token.end,'LIMIT_EXCEEDED')
        result=[]
        try:
            while self.token.kind!='EOF' and not self.is_name(*terminators):result.append(self.statement())
            return result
        finally:self.block_depth-=1

    def condition(self):
        self.expect('(');value=self.expression();self.expect(')');self.expect(';')
        return value

    def statement(self):
        token=self.token
        if token.kind!='NAME':
            code='UNSUPPORTED_SERIALIZED_OR_STATEMENT' if token.kind=='NUMBER' else 'UNSUPPORTED_STATEMENT'
            raise TM1SyntaxError(code,'TI section must contain explicit source statements, not serialized record layouts or another language.',token.start,token.end,'UNSUPPORTED')
        upper=token.value.upper()
        if upper=='IF':
            start=self.take();branches=[]
            condition=self.condition();body=self.block(('ELSEIF','ELSE','ENDIF'))
            branches.append({'condition':condition,'body':body})
            while self.is_name('ELSEIF'):
                self.take();condition=self.condition();body=self.block(('ELSEIF','ELSE','ENDIF'))
                branches.append({'condition':condition,'body':body})
            if self.is_name('ELSE'):
                self.take();self.expect(';');branches.append({'condition':None,'body':self.block(('ENDIF',))})
            finish=self.expect('NAME')
            if finish.value.upper()!='ENDIF':raise TM1SyntaxError('EXPECTED_ENDIF','Expected ENDIF for the complete IF block.',finish.start,finish.end)
            end=self.expect(';')
            return self.node('if',start.start,end.end,branches=branches)
        if upper=='WHILE':
            start=self.take();condition=self.condition();body=self.block(('END',))
            finish=self.expect('NAME')
            if finish.value.upper()!='END':raise TM1SyntaxError('EXPECTED_END','Expected END for the complete WHILE block.',finish.start,finish.end)
            end=self.expect(';')
            return self.node('while',start.start,end.end,condition=condition,body=body)
        if upper in {'ELSE','ELSEIF','ENDIF','END'}:
            raise TM1SyntaxError('UNEXPECTED_BLOCK_TERMINATOR','Unmatched TI block terminator.',token.start,token.end)
        if upper in _CONTROLS:
            self.take();end=self.expect(';')
            return self.node('control',token.start,end.end,name=upper)
        name=self.take()
        if self.token.kind=='=':
            self.take();value=self.expression();end=self.expect(';')
            return self.node('assignment',name.start,end.end,name=name.value,value=value)
        # Parse invocations with the expression parser, preserving their return use.
        self.index-=1
        if self.tokens[self.index+1].kind!='(':
            raise TM1SyntaxError('UNSUPPORTED_STATEMENT','Unresolved bare TI statement was not skipped.',name.start,name.end,'UNSUPPORTED')
        call=self.atom();end=self.expect(';')
        return self.node('invoke',name.start,end.end,call=call)

    def document(self):
        statements=self.block()
        self.expect('EOF')
        return statements
