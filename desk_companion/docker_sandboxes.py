"""Small official-CLI adapter. Project state and process supervision belong to callers."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

SHELL_KIT='docker.io/docker/sbx-kit-shell@sha256:a72c2ead8a2ab26ac552e4578ea0e23b5e777e81e8a90108e676be09cffa69a4'
DEPENDENCY_HOSTS=('github.com:443','codeload.github.com:443','objects.githubusercontent.com:443',
                  'registry.npmjs.org:443','pypi.org:443','files.pythonhosted.org:443')
NO_WINDOW=0x08000000 if os.name=='nt' else 0


def cli_environment():
    """Keep Docker's user login context, remove product RPC and common provider keys."""
    secrets={'OPENAI_API_KEY','ANTHROPIC_API_KEY','MIMO_API_KEY','DASHSCOPE_API_KEY','NPM_TOKEN','GITHUB_TOKEN','GH_TOKEN'}
    return {key:value for key,value in os.environ.items()
            if not key.upper().startswith(('DESK_','FEISHU_','LARK_')) and key.upper() not in secrets}


class SandboxError(RuntimeError):
    def __init__(self,code,message,*,operation='',unknown=False,exit_code=None):
        super().__init__(message)
        self.code,self.public_message,self.operation,self.outcome_unknown=code,message,operation,unknown
        self.exit_code=exit_code


def name(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}',value):
        raise ValueError('官方沙箱名称无效。')
    return value


def port(value):
    if type(value) is not int or not 1024<=value<=65535:raise ValueError('端口须为1024–65535的整数。')
    return value


class SandboxesClient:
    def __init__(self,*,program=None,runner=subprocess.run):
        self.runner=runner
        if program is None:
            program=shutil.which('sbx')
            candidate=Path(os.environ.get('LOCALAPPDATA',''))/'DockerSandboxes/bin/sbx.exe'
            if not program and candidate.is_file():program=str(candidate)
        self.program=str(program) if program else None

    def _call(self,args,*,timeout=30,mutating=False):
        if self.program is None:raise SandboxError('not_installed','未找到官方Docker Sandboxes，请先安装并登录。')
        try:
            result=self.runner([self.program,*args],capture_output=True,text=True,encoding='utf-8',errors='replace',
                               timeout=timeout,creationflags=NO_WINDOW,env=cli_environment())
        except subprocess.TimeoutExpired as exc:
            raise SandboxError('outcome_unknown' if mutating else 'query_timeout',
                '官方沙箱操作超时，请先核对原环境状态。' if mutating else '官方沙箱状态查询超时。',operation=args[0],unknown=mutating) from exc
        except OSError as exc:
            raise SandboxError('cli_unavailable','官方沙箱CLI不可用，请检查安装与本机运行权限。',operation=args[0]) from exc
        if result.returncode:
            # Raw CLI output may contain local configuration; public errors stay bounded.
            raise SandboxError('cli_failed','官方沙箱操作失败，请核对daemon、登录、版本和当前策略。',operation=args[0],unknown=mutating,exit_code=result.returncode)
        if len(result.stdout)>1024*1024:raise SandboxError('invalid_response','官方沙箱控制响应过大。',operation=args[0],unknown=mutating)
        return result.stdout

    def inventory(self):
        try:
            value=json.loads(self._call(['ls','--format','json']))
            if not isinstance(value,list) or any(not isinstance(v,dict) or not all(isinstance(v.get(k),str) for k in ('id','name','status')) for v in value):raise ValueError()
            return value
        except (ValueError,TypeError) as exc:raise SandboxError('invalid_response','官方沙箱清单格式无法识别。') from exc

    def owned(self,sandbox,ident):
        sandbox=name(sandbox)
        target=next((v for v in self.inventory() if v['name']==sandbox),None)
        if target is None:raise SandboxError('not_found','原项目环境不存在，请先核对，未重新创建。')
        if not isinstance(ident,str) or not ident or target['id']!=ident:
            raise SandboxError('identity_changed','沙箱身份与原项目记录不符，未操作同名环境。')
        return target

    def probe(self):
        try:
            version=self._call(['version'])
            match=re.search(r'\b[vV]?(\d+\.\d+\.\d+)\b',version)
            if not match:raise SandboxError('unsupported_version','无法识别官方沙箱版本。')
            parsed=tuple(map(int,match[1].split('.')))
            if parsed<(0,45,0):raise SandboxError('unsupported_version','当前版本不支持所需的v3环境。')
            self.inventory()
            return dict(ok=True,provider='docker_sandboxes',version=match[1],scope='control_plane',guest_verified=False)
        except SandboxError as exc:
            return dict(ok=False,provider='docker_sandboxes',scope='control_plane',guest_verified=False,error=exc.public_message,code=exc.code)

    def create(self,sandbox,owner,*,cpus=2,memory='2g'):
        sandbox=name(sandbox)
        if not isinstance(owner,str) or not re.fullmatch('[a-f0-9]{32}',owner):raise ValueError('项目环境归属编号无效。')
        if type(cpus) is not int or not 1<=cpus<=4 or memory not in {'1g','2g','4g'}:raise ValueError('项目环境资源规格无效。')
        if any(v['name']==sandbox for v in self.inventory()):raise SandboxError('already_exists','同名环境已存在，请核对原操作，未覆盖。')
        # create with an OCI workload and no PATH has no host workspace grant.
        self._call(['create','--name',sandbox,'--skills','off','--cpus',str(cpus),'--memory',memory,
                    '--env','TEBB_ENV_ID='+owner,SHELL_KIT],timeout=600,mutating=True)
        try:
            result=next(v for v in self.inventory() if v['name']==sandbox)
        except (SandboxError,StopIteration) as exc:
            raise SandboxError('outcome_unknown','已提交创建，尚未核对到原环境，请先观察原请求。',operation='create',unknown=True) from exc
        return result

    def exec_command(self,sandbox,ident,argv,*,interactive=False):
        """Return an in-guest command for the existing bounded process supervisor."""
        self.owned(sandbox,ident)
        if type(interactive) is not bool:raise ValueError('交互输入选项须为布尔值。')
        if not self.program:raise SandboxError('not_installed','未找到官方Docker Sandboxes。')
        if not isinstance(argv,list) or not argv or any(not isinstance(a,str) or '\0' in a for a in argv) or sum(map(len,argv))>24000:
            raise ValueError('项目命令参数无效或过长。')
        return [self.program,'exec',*(['-i'] if interactive else []),name(sandbox),*argv]

    def dependency_network(self,sandbox,ident):
        self.owned(sandbox,ident)
        self._call(['policy','allow','network','--sandbox',name(sandbox),','.join(DEPENDENCY_HOSTS)],mutating=True)
        checks=[]
        for host in DEPENDENCY_HOSTS:
            try:value=json.loads(self._call(['policy','check','network','--sandbox',name(sandbox),'--json',host]))
            except (ValueError,TypeError) as exc:raise SandboxError('invalid_response','官方网络策略回执无法识别。') from exc
            if not isinstance(value,dict) or type(value.get('allowed')) is not bool or value.get('context')!='sandbox:'+sandbox:
                raise SandboxError('invalid_response','官方网络策略范围或回执无法识别。')
            checks.append(dict(target=host,allowed=value['allowed']))
        if not all(value['allowed'] for value in checks):raise SandboxError('network_denied','依赖目标被现有Docker策略拒绝，未绕过全局或组织deny规则。')
        return dict(scope='single_sandbox',targets=checks,exclusive_allowlist=False,
                    effective_policy='merged with Docker global/organization and deny rules')

    def capture(self,sandbox,ident,argv,*,timeout=30):
        """Bounded fixed metadata commands; ordinary project jobs use the caller supervisor."""
        command=self.exec_command(sandbox,ident,argv)
        return self._call(command[1:],timeout=timeout,mutating=True)

    def publish(self,sandbox,ident,guest_port,host_port):
        self.owned(sandbox,ident)
        mapping=f'127.0.0.1:{port(host_port)}:{port(guest_port)}/tcp4'
        self._call(['ports',name(sandbox),'--publish',mapping],mutating=True)
        return dict(host='127.0.0.1',host_port=host_port,guest_port=guest_port,protocol='tcp4')

    def unpublish(self,sandbox,ident,guest_port,host_port):
        self.owned(sandbox,ident)
        self._call(['ports',name(sandbox),'--unpublish',f'127.0.0.1:{port(host_port)}:{port(guest_port)}/tcp4'],mutating=True)

    def stop(self,sandbox,ident):
        if self.owned(sandbox,ident)['status']!='stopped':self._call(['stop',name(sandbox)],timeout=60,mutating=True)
        if self.owned(sandbox,ident)['status']!='stopped':raise SandboxError('stop_unconfirmed','尚未确认项目环境已停止。',unknown=True)
        return dict(ok=True,state='stopped',sandbox_id=ident)
