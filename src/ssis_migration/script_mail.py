"""Translate a bounded C# mail entry point; never execute supplied script text."""
from __future__ import annotations
import hashlib
import json
import re

from ssis_migration.mapping import ConversionGap


def gap(message):
    raise ConversionGap('SCRIPT_TRANSLATION_REVIEW_REQUIRED',message)


def tokens(text):
    pattern=re.compile(r'\s+|//[^\n]*|/\*[\s\S]*?\*/|\#(?:end)?region[^\n]*|"(?:[^"\\]|\\.)*"|[A-Za-z_]\w*|\d+|!=|==|[{}()\[\].;=+:,]')
    result=[];pos=0
    while pos<len(text):
        match=pattern.match(text,pos)
        if not match:gap('Unsupported C# token or preprocessor directive.')
        word=match[0];pos=match.end()
        if word.isspace() or word.startswith(('//','/*','#')):continue
        if word.startswith('"'):
            try:word=('string',json.loads(word))
            except ValueError:gap('Unsupported C# string escape.')
        result.append(word)
        if len(result)>12000:gap('Script exceeds the bounded translator token limit.')
    return result


class Parser:
    def __init__(self,text):self.words=tokens(text);self.pos=0
    def peek(self):return self.words[self.pos] if self.pos<len(self.words) else None
    def take(self,word=None):
        value=self.peek()
        if value is None or word is not None and value!=word:gap('Unsupported C# mail structure.')
        self.pos+=1;return value
    def name(self):
        value=self.take()
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z_]\w*',value):gap('Expected a C# identifier.')
        return value
    def path(self):
        value=self.name()
        while self.peek()=='.':self.take('.');value+='.'+self.name()
        return value
    def args(self):
        self.take('(');args=[]
        if self.peek()!=')':
            args.append(self.expr())
            while self.peek()==',':self.take(',');args.append(self.expr())
        self.take(')');return args
    def expr(self,depth=0):
        if depth>24:gap('C# expression nesting exceeds the supported limit.')
        value=self.atom(depth+1)
        while self.peek()=='+':self.take('+');value=('plus',value,self.atom(depth+1))
        if self.peek() in ('==','!='):
            operator=self.take();value=('compare',operator,value,self.expr(depth+1))
        return value
    def atom(self,depth):
        value=self.take()
        if isinstance(value,tuple):node=value
        elif value=='new':node=('new',self.path(),self.args())
        elif value=='(':
            if self.peek()=='int':
                self.take('int');self.take(')');self.take('ScriptResults');self.take('.');self.take('Success')
                return ('success',)
            node=self.expr(depth+1);self.take(')')
        elif value in ('true','false'):node=('bool',value=='true')
        elif isinstance(value,str) and re.fullmatch(r'[A-Za-z_]\w*',value):node=('name',value)
        else:gap('Unsupported C# expression.')
        while self.peek() in ('.','[','('):
            if self.peek()=='.':self.take('.');node=('attr',node,self.name())
            elif self.peek()=='[':self.take('[');index=self.expr(depth+1);self.take(']');node=('index',node,index)
            else:node=('call',node,self.args())
        return node
    def block(self):
        self.take('{');result=[]
        while self.peek()!='}':result.append(self.statement())
        self.take('}');return result
    def statement(self):
        if self.peek()=='if':
            self.take('if');self.take('(');test=self.expr();self.take(')')
            return ('if',test,self.block() if self.peek()=='{' else [self.statement()])
        if self.peek() in ('string','MailMessage','SmtpClient'):
            kind=self.take();name=self.name();value=None
            if self.peek()=='=':self.take('=');value=self.expr()
            self.take(';');return ('declare',kind,name,value)
        target=self.expr()
        if self.peek()=='=':
            self.take('=');value=self.expr();self.take(';');return ('assign',target,value)
        self.take(';');return ('call',target)
    def program(self):
        while self.peek()=='using':
            self.take('using');self.path();self.take(';')
        self.take('namespace');self.path();self.take('{')
        self.take('[')
        if self.path() not in {'Microsoft.SqlServer.Dts.Tasks.ScriptTask.SSISScriptTaskEntryPointAttribute','SSISScriptTaskEntryPoint'}:
            gap('Unsupported Script Task entry-point attribute.')
        self.take(']');self.take('public');self.take('partial');self.take('class');self.take('ScriptMain');self.take(':')
        if self.path()!='Microsoft.SqlServer.Dts.Tasks.ScriptTask.VSTARTScriptObjectModelBase':gap('Unsupported script base class.')
        self.take('{');constants=[];main=None;enum_seen=False
        while self.peek()!='}':
            if self.peek()=='enum':
                if enum_seen:gap('Multiple script-result enums are unsupported.')
                enum_seen=True;self.take('enum');self.take('ScriptResults');self.take('{')
                for i,name in enumerate(('Success','Failure')):
                    if i:self.take(',')
                    self.take(name);self.take('=')
                    if self.path()!='Microsoft.SqlServer.Dts.Runtime.DTSExecResult.'+name:gap('Custom script-result values are unsupported.')
                if self.peek()==',':self.take(',')
                self.take('}')
                if self.peek()==';':self.take(';')
            elif self.peek()=='const':
                self.take('const');self.take('string');name=self.name();self.take('=');value=self.expr();self.take(';');constants.append((name,value))
            elif self.peek()=='public':
                if main is not None:gap('Additional public script methods are unsupported.')
                self.take('public');self.take('void');self.take('Main');self.take('(');self.take(')');main=self.block()
            else:gap('Additional script fields, constructors, or methods require explicit translation.')
        self.take('}');self.take('}')
        if self.peek() is not None or main is None or not enum_seen:gap('Incomplete or additional C# program structure.')
        return constants,main


def path(node):
    if node[0]=='name':return node[1]
    if node[0]=='attr':
        base=path(node[1]);return base+'.'+node[2] if base else None
    return None


class Compiler:
    def __init__(self,package,runtime,owner,inspect_only=False):
        self.package=package;self.runtime=runtime;self.owner=owner;self.symbols={};self.lines=[];self.sent=0;self.success=0;self.references=set()
        self.inspect_only=inspect_only
        self.prefix='_ssis_script_'+hashlib.sha256(owner.encode()).hexdigest()[:10]+'_'
    def line(self,value,indent=0):self.lines.append('    '*indent+value)
    def variable(self,name):
        if name not in self.symbols:gap('Script refers to an undeclared symbol.')
        return self.symbols[name]
    def expr(self,node):
        kind=node[0]
        if kind=='string':return repr(node[1]),'string',None
        if kind=='bool':return repr(node[1]),'bool',None
        if kind=='name':
            value=self.variable(node[1])
            if not value['initialized']:gap('Script reads an uninitialized local variable.')
            return self.prefix+node[1],value['kind'],value.get('origin')
        if kind=='plus':
            left,lt,_=self.expr(node[1]);right,rt,_=self.expr(node[2])
            if lt!=rt or lt!='string':gap('Only string concatenation is supported in a mail script.')
            return '('+left+' + '+right+')','string',None
        if path(node) in ('String.Empty','string.Empty'):return "''",'string',None
        if kind=='compare':
            a,at,_=self.expr(node[2]);b,bt,_=self.expr(node[3])
            if at!=bt or at!='string':gap('Only string equality conditions are supported in a mail script.')
            return '('+a+' '+node[1]+' '+b+')','bool',None
        if kind=='call':
            target,args=node[1:]
            if target[0]=='attr' and target[2]=='ToString' and not args:
                value=target[1]
                if value[0]=='attr' and value[2]=='Value' and value[1][0]=='index' and path(value[1][1])=='Dts.Variables' and value[1][2][0]=='string':
                    reference=value[1][2][1];key,vt=self.runtime.variable(reference,self.owner)
                    self.references.add(reference)
                    code=self.runtime.read(reference,self.owner)
                    if vt in ('int16','int32','int64'):return 'str('+code+')','string',None
                    if vt!='string':gap('This Dts variable ToString needs unsupported source culture/conversion semantics.')
                    return code,'string',None
                if value[0]=='call' and value[1][0]=='attr' and value[1][2]=='GetValue' and len(value[2])==1:
                    prop=value[1][1];connection=value[2][0]
                    if (prop[0]=='index' and prop[2]==('string','SmtpServer') and prop[1]==('attr',connection,'Properties')
                            and connection[0]=='index' and path(connection[1])=='Dts.Connections' and connection[2][0]=='string'):
                        name=connection[2][1]
                        if self.inspect_only:return "''",'string','source_mail_connection'
                        profiles=self.package.metadata.get('smtp_connections',{})
                        matches=[(ref,p) for ref,p in profiles.items() if ref==name or p.get('name')==name]
                        if len(matches)!=1 or not re.fullmatch(r'[A-Za-z0-9_.-]+',str(matches[0][1].get('host') or '')):
                            raise ConversionGap('SMTP_METADATA_REQUIRED','Resolve the script SMTP connection name and captured host uniquely.')
                        return repr(matches[0][1]['host']),'string',matches[0][0]
            if target[0]=='attr' and target[2]=='Replace' and len(args)==2:
                if args[0][0]!='string' or not args[0][1]:gap('Mail replacement needs a nonempty literal search string.')
                base,bt,_=self.expr(target[1]);old,ot,_=self.expr(args[0]);new,nt,_=self.expr(args[1])
                if (bt,ot,nt)!=('string','string','string'):gap('Mail body replacement requires strings.')
                return base+'.replace('+old+', '+new+')','string',None
        if kind=='new':
            name,args=node[1:];name=name.removeprefix('System.Net.Mail.')
            if name=='MailMessage' and not args:
                return "{'to':[], 'cc':[], 'bcc':[], 'subject':'', 'body':'', 'html':False, 'from':None}",'mail',None
            if name=='MailAddress' and len(args)==1:
                code,ct,_=self.expr(args[0])
                if ct=='string':return code,'address',None
            if name=='SmtpClient' and len(args)==1:
                code,ct,origin=self.expr(args[0])
                if ct=='string' and origin:return '('+code+', 25)','smtp',origin
        gap('Unsupported C# mail expression or connection access.')
    def set_local(self,name,node,indent):
        value=self.variable(name);code,kind,origin=self.expr(node)
        if kind!=value['kind']:gap('Script local assignment changes type.')
        value.update(initialized=True,origin=origin);self.line(self.prefix+name+' = '+code,indent)
    def statements(self,items,indent=0,conditional=False):
        for stmt in items:
            kind=stmt[0]
            if kind=='declare' and not conditional:
                _,cs_type,name,value=stmt
                if name in self.symbols:gap('Duplicate C# declaration.')
                self.symbols[name]={'kind':{'string':'string','MailMessage':'mail','SmtpClient':'smtp'}[cs_type],'initialized':False}
                if value is not None:self.set_local(name,value,indent)
            elif kind=='if' and not conditional:
                code,ct,_=self.expr(stmt[1])
                if ct!='bool' or not stmt[2]:gap('Unsupported empty/non-Boolean mail condition.')
                self.line('if '+code+':',indent);self.statements(stmt[2],indent+1,True)
            elif kind=='assign':
                target,value=stmt[1:]
                if target[0]=='name' and not conditional:self.set_local(target[1],value,indent)
                elif path(target)=='Dts.TaskResult' and value==('success',) and not conditional:
                    self.success+=1
                    if self.sent!=1 or stmt is not items[-1]:gap('Task success must follow exactly one unconditional send.')
                elif target[0]=='attr' and target[1][0]=='name':
                    name=target[1][1];record=self.variable(name);field=target[2]
                    expected={'From':('from','address'),'Subject':('subject','string'),'Body':('body','string'),'IsBodyHtml':('html','bool')}
                    if record['kind']!='mail' or not record['initialized'] or field not in expected:gap('Unsupported mail/client property assignment.')
                    slot,vt=expected[field];code,ct,_=self.expr(value)
                    if ct!=vt:gap('Mail property value has an unsupported type.')
                    self.line(f'{self.prefix+name}[{slot!r}] = {code}',indent)
                else:gap('Unsupported script assignment.')
            elif kind=='call':
                call=stmt[1]
                if call[0]!='call' or call[1][0]!='attr':gap('Unsupported script statement.')
                target,args=call[1:];member=target[2];base=target[1]
                if member=='Add' and base[0]=='attr' and base[1][0]=='name' and base[2] in ('To','CC','Bcc') and len(args)==1:
                    name=base[1][1];record=self.variable(name);code,ct,_=self.expr(args[0])
                    if record['kind']!='mail' or not record['initialized'] or ct!='string':gap('Unsupported recipient collection.')
                    self.line(f'{self.prefix+name}[{base[2].lower()!r}].append({code})',indent)
                elif member=='Send' and base[0]=='name' and len(args)==1 and args[0][0]=='name' and not conditional:
                    smtp=base[1];mail=args[0][1]
                    if self.variable(smtp)['kind']!='smtp' or not self.variable(smtp)['initialized'] or self.variable(mail)['kind']!='mail' or not self.variable(mail)['initialized']:
                        gap('Unsupported mail send receiver/argument.')
                    self.send(self.prefix+smtp,self.prefix+mail,indent);self.sent+=1
                else:gap('Unsupported C# call; mail translation cannot omit side effects.')
            else:gap('Unsupported conditional or local C# statement.')
    def send(self,smtp,mail,indent):
        msg=self.prefix+'message';recipients=self.prefix+'recipients'
        for line in [f'{msg} = EmailMessage()',f'{msg}["From"] = {mail}["from"]',
                f'{msg}["Subject"] = {mail}["subject"]',f'{msg}["To"] = ",".join({mail}["to"])',
                f'if {mail}["cc"]: {msg}["Cc"] = ",".join({mail}["cc"])',
                f'{msg}.set_content({mail}["body"], subtype="html" if {mail}["html"] else "plain")',
                f'if any(";" in value for value in {mail}["to"] + {mail}["cc"] + {mail}["bcc"]):',
                "    raise RuntimeError('Source MailAddressCollection requires comma-separated recipients.')",
                f'{recipients} = [address for _, address in getaddresses({mail}["to"] + {mail}["cc"] + {mail}["bcc"])]',
                f'if not {recipients} or not all({recipients}) or not parseaddr({mail}["from"])[1]:',
                "    raise RuntimeError('Source mail sender/recipients are invalid or missing.')",
                f'with smtplib.SMTP({smtp}[0], {smtp}[1], timeout=30) as _ssis_script_transport:',
                f'    _ssis_script_transport.send_message({msg}, from_addr=parseaddr({mail}["from"])[1], to_addrs={recipients})']:
            self.line(line,indent)


def translate_script_mail(exe,package,runtime,*,inspect_only=False):
    generated={'properties/settings.designer.cs','properties/resources.designer.cs','properties/assemblyinfo.cs'}
    for source in exe.properties.get('script_sources',[]):
        name=source.get('name','').replace('\\','/').casefold()
        if name.split('/')[-1]!='scriptmain.cs' and (name not in generated or source.get('status')!='CAPTURED'
                or 'ModuleInitializer' in (source.get('text') or '')):
            gap('Additional or uninspected project source requires explicit translation.')
    sources=[s for s in exe.properties.get('script_sources',[]) if s.get('name','').replace('\\','/').split('/')[-1].casefold()=='scriptmain.cs'
        and s.get('status')=='CAPTURED' and s.get('text')]
    if len(sources)!=1:gap('One captured C# ScriptMain.cs is required for bounded mail translation.')
    try:constants,main=Parser(sources[0]['text']).program()
    except RecursionError:gap('Script nesting exceeds the supported limit.')
    candidate=runtime.fork();compiler=Compiler(package,candidate,exe.ref_id,inspect_only=inspect_only)
    compiler.lines=['import smtplib','from email.message import EmailMessage','from email.utils import getaddresses, parseaddr']
    try:
        for name,value in constants:
            if name in compiler.symbols:gap('Duplicate C# constant.')
            compiler.symbols[name]={'kind':'string','initialized':False}
            compiler.set_local(name,value,0)
        compiler.statements(main)
    except RecursionError:gap('Script expression nesting exceeds the supported limit.')
    if compiler.sent!=1 or compiler.success!=1:gap('Expected one unconditional send followed by source task success.')
    if any(name in exe.properties for name in ('ReadOnlyVariables','ReadWriteVariables')):
        declared={v.strip() for name in ('ReadOnlyVariables','ReadWriteVariables') for v in exe.properties.get(name,'').split(',') if v.strip()}
        if not compiler.references.issubset(declared):gap('Script references variables outside its captured access declarations.')
    if inspect_only:return None
    runtime.adopt(candidate)
    return '\n'.join(compiler.lines)
