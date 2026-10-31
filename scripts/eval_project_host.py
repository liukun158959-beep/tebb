"""Isolated evaluation parent using the product RPC/queue, without schedulers."""
import argparse
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from desk_companion.agent_eval_projects import BUG,TEST,TEST_COMMAND,PYTHON_START,REPOSITORIES,atomic_json,read_json,receipts_bound_to_environment,final_test_receipt,active_tool_marker,original_cancel_command
from desk_companion.collaboration import Collaboration
from desk_companion.docker_sandboxes import SandboxesClient
from desk_companion.local_api import server
from desk_companion.local_api.host import HeadlessApp
from desk_companion.project_environments import ProjectEnvironmentService


def snapshot(host):
    tasks=[manager.get(item['id'])for manager in host.collaboration.managers.values()for item in manager.list()['items']]
    environments=host.project_environments.store.list();marker=False
    with host.project_environments.lock:
        live=[dict(operation=key,pid=value.pid)for key,value in host.project_environments.processes.items()if value.poll()is None]
    active={value['operation']for value in live}
    for environment in environments:
        operation=environment.get('active_operation')
        if not operation:continue
        record=host.project_environments.store.operation(operation)
        if record['kind']=='execute'and record['owner_task']and record['state']=='running'and operation in active:
            log=host.project_environments.log(environment['id'],environment['scope'],operation)
            marker=marker or active_tool_marker(base64.b64decode(log['base64']))
    return dict(runs=host.collaboration.snapshot()['state']['runs'],tasks=tasks,environments=environments,
                active_guest_marker=marker,owned_foreground_processes=live)


def verify(host,folder,scenario,variant,request,setup):
    service=host.project_environments;state=snapshot(host)
    root=next(value for value in state['runs']if value['id']==request['run_id'])
    receipts=root.get('project_receipts',[])
    prepare=next((value for value in receipts if value['tool']=='project_prepare'and value['ok']),{})
    ident=setup.get('environment')or prepare.get('environment',{}).get('id')
    proof=dict(identity_matches=False,sdk_stopped=False,verification_operations=[])
    if not ident:return dict(**proof,error='model did not produce an environment')
    environment=service.store.get(ident);scope=environment['scope'];client=service.client
    proof.update(environment_id=ident,sandbox_id=environment['sandbox_id'],repository=environment['repository'],
                 observed_commit=environment.get('commit'),identity_matches=scope=='conversation:dm-research')
    def runtime():return client.owned(environment['sandbox_name'],environment['sandbox_id'])
    if scenario=='cancel':
        if not(request.get('cancel_requested')and request.get('active_guest_marker')and root.get('state')=='cancelled'):
            return dict(**proof,fallback_cleanup_used=True,error='no active tool cancellation was requested and confirmed')
        deadline=time.monotonic()+75
        while time.monotonic()<deadline:
            environment=service.store.get(ident)
            operation=environment.get('active_operation')or environment.get('latest_operation')
            record=service.store.operation(operation)if operation else{}
            observed=runtime()
            if record.get('state')=='cancelled'and observed['status']=='stopped':
                task_ids={value['id']for value in state['tasks']if value.get('session')=='collab-'+root['id']}
                proof['identity_matches']=proof['identity_matches']and record.get('environment')==ident and record.get('owner_task')in task_ids
                proof.update(sdk_stopped=True,cancelled_operation=record,actual_runtime=observed,
                             original_tool_command=original_cancel_command(state['tasks'],record.get('owner_task')));break
            time.sleep(.3)
        if not proof['sdk_stopped']:proof['fallback_cleanup_used']=True
        return proof
    proof['identity_matches']=proof['identity_matches']and receipts_bound_to_environment(receipts,ident)
    if scenario=='coding':
        tests=service.read(ident,scope,uuid.uuid4().hex,'test_eval_math.py')
        implementation=service.read(ident,scope,uuid.uuid4().hex,'eval_math.py')
        proof.update(original_tests_unchanged=tests.get('sha256')==hashlib.sha256(TEST.encode()).hexdigest(),
            original_test_sha256=hashlib.sha256(TEST.encode()).hexdigest(),actual_test_sha256=tests.get('sha256'),
            implementation_changed=bool(implementation.get('sha256'))and implementation['sha256']!=hashlib.sha256(BUG.encode()).hexdigest())
        test_receipt=final_test_receipt(receipts,TEST_COMMAND)
        proof['model_test_exit_code']=test_receipt.get('exit_code')
        result=service.execute(ident,scope,uuid.uuid4().hex,TEST_COMMAND,timeout=30)
        proof['verification_operations'].append(result['operation']['id']);proof['verification_test_exit_code']=result.get('exit_code')
    else:
        repository,ref=REPOSITORIES[variant]
        proof['checkout_matches']=environment['repository']==repository and environment.get('commit')==ref and environment.get('dependencies')=='installed'
        starts=[value for value in receipts if value['tool']=='project_start'and value['ok']and value.get('service_ready')]
        started=starts[-1]if starts else{};svc=started.get('service',{});health=svc.get('health',{})
        expected_command='npm start'if variant=='node'else PYTHON_START
        proof.update(expected_start_sha256=hashlib.sha256(expected_command.encode()).hexdigest(),actual_start_sha256=started.get('command_sha256'))
        proof['original_service_command']=proof['actual_start_sha256']==proof['expected_start_sha256']
        proof['http_ready_before_stop']=bool(health.get('ok')and health.get('status')==200 and svc.get('url','').startswith('http://127.0.0.1:'))
        stopped=next((value for value in receipts if value['tool']=='project_stop'and value['ok']),{})
        proof['model_stop_same_environment']=stopped.get('environment',{}).get('id')==ident
        initial=runtime();proof['model_vm_stopped_before_verification']=initial['status']=='stopped'
        proof['model_stop_same_environment']=proof['model_stop_same_environment']and proof['model_vm_stopped_before_verification']
        if svc.get('operation'):
            log=service.log(ident,scope,svc['operation']);data=base64.b64decode(log['base64'])
            proof.update(actual_log_bytes=len(data),actual_log_sha256=hashlib.sha256(data).hexdigest(),service=svc)
        if svc.get('host_port'):
            proof['preview_unreachable']=not service._http(svc['host_port']).get('ok')
        code=('import json,subprocess;from pathlib import Path;'
            'p=Path("/home/agent/project");'
            'commit=subprocess.check_output(["git","-C",str(p),"rev-parse","HEAD"],text=True,timeout=4).strip();'
            'changes=subprocess.check_output(["git","-C",str(p),"status","--porcelain","--untracked-files=no"],text=True,timeout=8);'
            'print(json.dumps(dict(commit=commit,tracked_changes=changes)))\n# TEBB_EVAL_INSPECT_CHECKOUT')
        metadata=json.loads(client.capture(environment['sandbox_name'],environment['sandbox_id'],['python3','-I','-c',code]))
        proof.update(checkout_observation=metadata,tracked_files_unchanged=metadata['commit']==ref and not metadata['tracked_changes'])
    stopped=service.stop(ident,scope);proof['verification_stop']=stopped
    proof.update(sdk_stopped=bool(stopped['ok']and runtime()['status']=='stopped'))
    return proof


async def main(args):
    from websockets.asyncio.server import serve
    folder=Path(args.directory).resolve();host=HeadlessApp();server.HOST=host;server.TOKEN=args.token;server.SHUTDOWN=asyncio.Event()
    client=SandboxesClient()
    if args.sdk=='fixture':
        from eval_project_fixture import FixtureSDK
        client=FixtureSDK(folder/'fixture_sdk',args.variant)
    host._collaboration=Collaboration(path=folder/'workspace.sqlite3')
    for manager in host.collaboration.managers.values():manager.configure(1,min(args.timeout,90),args.timeout,False)
    host._project_environments=ProjectEnvironmentService(folder/'projects',client=client,owner_active=host._project_task_active)
    service=host.project_environments;setup=dict(provider_mode=args.sdk,setup_operations=[])
    try:
        probe=await asyncio.to_thread(client.probe);setup['sdk_probe']=probe
        if not probe['ok']:
            missing=probe.get('code')in{'not_installed','unsupported_version','cli_unavailable','cli_failed'}
            atomic_json(folder/'setup-error.json',dict(prerequisite_missing=missing,error=probe.get('error','SDK unavailable'),probe=probe));return
        if args.scenario in{'coding','cancel'}:
            repository,ref=REPOSITORIES[args.variant]
            prepared=await asyncio.to_thread(service.prepare,'conversation:dm-research',uuid.uuid4().hex,repository,ref=ref)
            if not prepared['ok']:raise RuntimeError(prepared.get('error','project fixture preparation failed'))
            ident=prepared['environment']['id'];setup.update(environment=ident,setup_operations=[prepared['operation']['id']])
            if args.scenario=='coding':
                for name,text in [('eval_math.py',BUG),('test_eval_math.py',TEST)]:
                    written=await asyncio.to_thread(service.write,ident,'conversation:dm-research',uuid.uuid4().hex,name,text)
                    if not written['ok']:raise RuntimeError(written.get('error','synthetic input write failed'))
                    setup['setup_operations'].append(written['operation']['id'])
        verification=None
        async with serve(server._handler,'127.0.0.1',args.port,process_request=server._health):
            atomic_json(folder/'ready.json',setup)
            try:
                while not server.SHUTDOWN.is_set():
                    atomic_json(folder/'progress.json',snapshot(host))
                    if verification is None and(folder/'verify-request.json').exists():
                        verification=asyncio.create_task(asyncio.to_thread(verify,host,folder,args.scenario,args.variant,read_json(folder/'verify-request.json'),setup))
                    if verification is not None and verification.done()and not(folder/'verification.json').exists():
                        try:result=verification.result()
                        except Exception as exc:result=dict(verified=False,error_type=type(exc).__name__,error=str(exc))
                        atomic_json(folder/'verification.json',result)
                    await asyncio.sleep(.1)
            finally:
                service.closed=True
                cleanup=asyncio.create_task(asyncio.to_thread(service.shutdown))
        await asyncio.to_thread(host.collaboration.shutdown)
        result=await cleanup
        atomic_json(folder/'cleanup.json',dict(sdk_stopped=result['ok'],result=result))
    except Exception as exc:
        atomic_json(folder/'setup-error.json',dict(prerequisite_missing=False,error_type=type(exc).__name__,error=str(exc)))
        raise
    finally:
        service.closed=True
        if not(folder/'cleanup.json').exists():
            result=await asyncio.to_thread(service.shutdown)
            atomic_json(folder/'cleanup.json',dict(sdk_stopped=result['ok'],result=result))
        await asyncio.to_thread(host.collaboration.shutdown)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,required=True);parser.add_argument('--token',required=True)
    parser.add_argument('--directory',required=True);parser.add_argument('--timeout',type=int,required=True)
    parser.add_argument('--scenario',choices=['coding','clone','cancel'],required=True)
    parser.add_argument('--variant',choices=['node','python'],required=True);parser.add_argument('--sdk',choices=['fixture','official'],required=True)
    parsed=parser.parse_args()
    if not parsed.token or not 20<=parsed.timeout<=1200:parser.error('nonempty token and timeout 20..1200 required')
    asyncio.run(main(parsed))
