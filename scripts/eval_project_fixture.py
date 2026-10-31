"""Evaluation-only SDK substitute; never runs model-generated shell on the host."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import shlex
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from desk_companion.docker_sandboxes import SandboxError
from desk_companion.agent_eval_projects import FIX,TEST,TEST_COMMAND,HOLD_COMMAND,PYTHON_SETUP,PYTHON_START


def path_in(root,value):
    root=Path(root).resolve();path=(root/value).resolve()
    if not path.is_relative_to(root):raise ValueError('fixture path outside private root')
    return path


class FixtureSDK:
    def __init__(self,path,variant):
        self.path=Path(path).resolve();self.path.mkdir(parents=True,exist_ok=True)
        self.variant=variant;self.items={};self.mapping={}
    def probe(self):return dict(ok=True,provider='fixture',scope='synthetic_sdk',guest_verified=False)
    def inventory(self):return list(self.items.values())
    def owned(self,name,ident):
        item=self.items.get(name)
        if not item or item['id']!=ident:raise SandboxError('identity_changed','fixture identity mismatch')
        return dict(item)
    def create(self,name,owner,**kwargs):
        if name in self.items:raise SandboxError('already_exists','fixture already exists')
        item=dict(name=name,id='fixture-'+uuid.uuid4().hex,status='running',agent='sbx-kit-shell',owner=owner)
        self.items[name]=item;return dict(item)
    def dependency_network(self,name,ident):
        self.owned(name,ident);return dict(scope='fixture',exclusive_allowlist=False)
    def stop(self,name,ident):
        self.owned(name,ident);self.items[name]['status']='stopped'
        (self.path/ident/'fixture-stop').write_text('stop','utf-8')
        return dict(ok=True,state='stopped',sandbox_id=ident)
    def publish(self,name,ident,guest_port,host_port):self.owned(name,ident);self.mapping[name]=host_port
    def unpublish(self,name,ident,guest_port,host_port):self.owned(name,ident);self.mapping.pop(name,None)
    def capture(self,name,ident,argv,**kwargs):
        item=self.owned(name,ident)
        if argv[:3]==['python3','-I','-c']and 'TEBB_EVAL_INSPECT_CHECKOUT' in argv[-1]:
            root=self.path/ident
            clean=(root/'README.md').read_text('utf-8')=='Synthetic SDK fixture; not a downloaded repository.'
            return json.dumps(dict(commit=self.commit(),tracked_changes=''if clean else' M README.md'))
        if argv[:3]!=['python3','-I','-c'] or 'TEBB_ENV_ID' not in argv[-1]:raise ValueError('unsupported fixed fixture metadata')
        return json.dumps(dict(owner=item['owner'],project_exists=True,repository_matches=True,commit=self.commit()))
    def commit(self):return '49943b5a94413a530761350f04e896f76e8d620e' if self.variant=='python' else '8b8dec11fcf027522a857e0c831ee01b7124f673'
    def exec_command(self,name,ident,argv,*,interactive=False):
        self.owned(name,ident);self.items[name]['status']='running'
        if interactive:
            from desk_companion.project_integrity import RUNNER,GIT_PROBE
            if argv[:4]!=['python3','-I','-u','-c']or argv[-2]!=RUNNER:raise ValueError('unsupported fixture control wrapper')
            config=json.loads(base64.b64decode(argv[-1]));script=config['argv'][2]
            command=shlex.split(script.split('\nexec bash -c ',1)[1])
            if len(command)!=1 or command[0]not in {'npm start',PYTHON_START}:raise ValueError('unsupported fixture service command')
            root=self.path/ident;(root/'fixture-stop').unlink(missing_ok=True)
            config.update(project=str(root),argv=[sys.executable,str(Path(__file__).resolve()),'--child',
                json.dumps(dict(action='serve',root=str(root),variant=self.variant,port=self.mapping[name]))])
            probe='''
def inspect_source(project,baseline,owner):
    import time
    from pathlib import Path
    clean=(Path(project)/'README.md').read_text('utf-8')=='Synthetic SDK fixture; not a downloaded repository.'
    now=time.time()
    return dict(complete=True,unchanged=clean,scope='tracked_files_at_observation',untracked_excluded=True,
        baseline_commit=baseline,current_commit=baseline,tracked_changes=not clean,head_matches_baseline=True,
        owner=owner,observed_at=now,observation_finished_at=now,fixture=True)
'''
            runner='import os;os.environ["TEBB_ENV_ID"]='+repr(self.items[name]['owner'])+'\n'+RUNNER.replace(GIT_PROBE,probe)
            return [sys.executable,'-I','-u','-c',runner,base64.b64encode(json.dumps(config).encode()).decode()]
        if argv[:2]!=['bash','-lc']:raise ValueError('fixture accepts only the production wrapper')
        script=argv[2];instruction=dict(root=str(self.path/ident),variant=self.variant)
        if script.startswith('set -eu\nexport GIT_TERMINAL_PROMPT=0 '):instruction.update(action='clone',commit=self.commit())
        elif script.startswith('set -eu\ncd /home/agent/project\nif test -f package.json;'):instruction.update(action='echo',text='fixture dependencies installed')
        elif script.startswith('cd /home/agent/project\npython3 -I -c ') and 'info=dict(language=' in script:
            instruction.update(action='runtime')
        elif '\nexec bash -c ' in script:
            command=shlex.split(script.split('\nexec bash -c ',1)[1])
            if len(command)!=1 or command[0] not in {'npm start',PYTHON_START}:raise ValueError('unsupported fixture service command')
            instruction.update(action='serve',port=self.mapping[name])
        else:
            prefix='set -eu\nmkdir -p /home/agent/project\ncd /home/agent/project\n'
            command=script[len(prefix):] if script.startswith(prefix) else script
            if command==TEST_COMMAND:instruction.update(action='test')
            elif command==HOLD_COMMAND:instruction.update(action='hold')
            elif command==PYTHON_SETUP:instruction.update(action='echo',text='fixture tutorial installed and database initialized')
            else:
                parts=shlex.split(command)
                if parts[:3]!=['python3','-I','-c'] or len(parts)!=4:raise ValueError('fixture refuses arbitrary model command')
                tree=ast.parse(parts[3]);path=None;encoded=None
                for node in ast.walk(tree):
                    if isinstance(node,ast.BinOp) and isinstance(node.left,ast.Name) and node.left.id=='root' and isinstance(node.op,ast.Div) and isinstance(node.right,ast.Constant):path=node.right.value
                    if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name) and node.func.value.id=='base64' and node.func.attr=='b64decode' and len(node.args)==1 and isinstance(node.args[0],ast.Constant):encoded=node.args[0].value
                if not isinstance(path,str):raise ValueError('fixture metadata path missing')
                path_in(instruction['root'],path)
                if encoded is None:instruction.update(action='read',path=path)
                else:
                    base64.b64decode(encoded,validate=True);instruction.update(action='write',path=path,base64=encoded)
        return [sys.executable,'-u',str(Path(__file__).resolve()),'--child',json.dumps(instruction)]


def child(value):
    root=Path(value['root']);root.mkdir(parents=True,exist_ok=True);action=value['action']
    if action=='clone':
        (root/'README.md').write_text('Synthetic SDK fixture; not a downloaded repository.','utf-8')
        (root/'package.json' if value['variant']=='node' else root/'pyproject.toml').write_text('{}','utf-8')
        print(value['commit'])
    elif action=='echo':print(value['text'])
    elif action=='runtime':print(json.dumps(dict(language=value['variant'],python_command='.venv/bin/python',activate='source .venv/bin/activate',fixture=True)))
    elif action=='read':
        path=path_in(root,value['path'])
        if path.is_dir():result=dict(path=value['path'],kind='directory',entries=[dict(name=p.name,type='directory' if p.is_dir() else 'file')for p in sorted(path.iterdir())][:200],truncated=False)
        else:
            data=path.read_bytes();assert len(data)<=65536
            result=dict(path=value['path'],kind='file',base64=base64.b64encode(data).decode(),sha256=hashlib.sha256(data).hexdigest())
        print(json.dumps(result))
    elif action=='write':
        path=path_in(root,value['path']);path.parent.mkdir(parents=True,exist_ok=True);data=base64.b64decode(value['base64'],validate=True)
        path.write_bytes(data);print(json.dumps(dict(path=value['path'],bytes=len(data),sha256=hashlib.sha256(data).hexdigest())))
    elif action=='test':
        # Simulated SDK exit, never import/execute model-authored Python on host.
        unchanged=(root/'test_eval_math.py').read_text('utf-8')==TEST
        fixed=(root/'eval_math.py').read_text('utf-8')==FIX
        print('SYNTHETIC SDK test result: '+('OK'if unchanged and fixed else'FAILED'))
        return 0 if unchanged and fixed else 1
    elif action=='hold':
        import time
        print('tebb-eval-tool-active',flush=True);time.sleep(90)
    elif action=='serve':
        from http.server import BaseHTTPRequestHandler,HTTPServer
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.end_headers();self.wfile.write(b'Synthetic SDK project fixture')
            def log_message(self,*args):pass
        print('SYNTHETIC SDK server',flush=True);server=HTTPServer(('127.0.0.1',value['port']),Handler);server.timeout=.1
        try:
            while not(root/'fixture-stop').exists():server.handle_request()
        finally:server.server_close()
    else:raise ValueError('unsupported fixture child operation')
    return 0


if __name__=='__main__':
    assert sys.argv[1]=='--child'
    raise SystemExit(child(json.loads(sys.argv[2])))
