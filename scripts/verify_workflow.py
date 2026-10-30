"""Authenticated API -> durable workflow -> real task process -> local fake model.

Never uses a real model or Feishu. Creates a fresh profile under --output.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.smoke_release import ModelServer, ThreadingHTTPServer, free_port
from websockets.asyncio.client import connect


async def verify(port, token, model_port):
    async def rpc(method, args=None):
        async with connect(f'ws://127.0.0.1:{port}/ws?token={token}', proxy=None) as socket:
            await socket.send(json.dumps(dict(type='rpc', id='verify', method=method, args=args or {})))
            envelope = json.loads(await asyncio.wait_for(socket.recv(), 30))['result']
            assert envelope['ok'], envelope
            result = envelope['result']
            assert result['ok'], result
            return result
    async def workflow(operation, payload=None):
        return await rpc('automation_workflow', dict(operation=operation, payload=payload or {}))
    await rpc('save_model_network', dict(payload=dict(mode='direct')))
    models = await rpc('save_model_entry', dict(payload=dict(base_url=f'http://127.0.0.1:{model_port}/v1', model='release-smoke', api_key='workflow-smoke-only')))
    model_id = next(item['id'] for item in models['items'] if item['model'] == 'release-smoke')
    graph = dict(nodes=[
        dict(id='trigger', kind='trigger', config=dict(text='process this'), position=dict(x=0,y=0)),
        dict(id='ai', kind='ai', config=dict(prompt='处理资料：{{trigger.text}}',model_id=model_id), position=dict(x=0,y=120)),
        dict(id='check', kind='condition', config=dict(left='{{ai.text}}',operator='contains',right='正常'), position=dict(x=0,y=240)),
        dict(id='selected', kind='condition', config=dict(left='{{ai.text}}',operator='not_empty',right=''), position=dict(x=0,y=360)),
        dict(id='skipped', kind='condition', config=dict(left='',operator='not_empty',right=''), position=dict(x=220,y=360)),
    ], edges=[dict(source='trigger',target='ai',branch=''),dict(source='ai',target='check',branch=''),
              dict(source='check',target='selected',branch='true'),dict(source='check',target='skipped',branch='false')])
    definition = (await workflow('save',dict(name='隔离工作流验收',graph=graph)))['definition']
    request = dict(id=definition['id'],version=definition['version'],request_id='workflow-smoke-stable',mode='full',input=dict(text='固定测试资料'))
    accepted = (await workflow('start',request))['run']
    duplicate = (await workflow('start',request))['run']
    assert accepted['id'] == duplicate['id'] and accepted['task_id'] == duplicate['task_id']
    deadline = time.monotonic()+80
    while time.monotonic()<deadline:
        run = (await workflow('get',dict(run_id=accepted['id'])))['run']
        if run['state'] not in {'queued','running'}:
            break
        await asyncio.sleep(.2)
    assert run['state']=='completed',run
    assert run['receipts']['ai']['output']['text']=='发布包对话正常。',run
    assert run['receipts']['selected']['state']=='confirmed'
    assert run['receipts']['skipped']['state']=='skipped'
    assert len(ModelServer.requests)==1,ModelServer.requests
    task=(await rpc('get_agent_task',dict(task_id=accepted['task_id'],focus=False)))['task']
    assert any(e['kind']=='tool_start' for e in task['events'])
    assert any(e['kind']=='llm_start' for e in task['events'])
    # A genuine legacy job can still be saved; scheduling snapshots the workflow version.
    schedule=dict(name='隔离定时',action='workflow',enabled=False,cadence='daily',weekdays=[],hour=9,minute=0,doc='',workflow_id=definition['id'],workflow_version=definition['version'],workflow_write_version='')
    await rpc('save_automation_job',dict(payload=schedule))
    await rpc('save_automation_job',dict(payload=dict(name='保留复盘',action='retro_gen',enabled=False,cadence='daily',weekdays=[],hour=9,minute=0,doc='')))
    jobs=(await rpc('list_automation_jobs'))['items']
    assert {j['action'] for j in jobs}=={'workflow','retro_gen'}
    return dict(ok=True,workflow_id=definition['id'],version=definition['version'],run_id=accepted['id'],task_id=accepted['task_id'],
                state=run['state'],model_calls=len(ModelServer.requests),node_states={key:value['state'] for key,value in run['receipts'].items()},
                real_model=False,feishu_writes=False)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--serve-seconds',type=int,default=0,help='Keep the isolated backend for local browser QA (0–300 seconds).')
    args=parser.parse_args()
    if not 0 <= args.serve_seconds <= 300:
        raise ValueError('--serve-seconds 必须为 0–300 秒。')
    output=args.output.resolve()
    if output.exists():
        raise ValueError('--output 必须是新的隔离目录。')
    output.mkdir(parents=True)
    profile=output/'profile';profile.mkdir()
    model=ThreadingHTTPServer(('127.0.0.1',0),ModelServer)
    thread=threading.Thread(target=model.serve_forever,daemon=True);thread.start()
    port,token=free_port(),secrets.token_hex(24)
    environment={**os.environ,'DESK_COMPANION_DATA_DIR':str(profile),'PYTHONUTF8':'1',
                 'PYTHONPATH':str(ROOT)+os.pathsep+os.environ.get('PYTHONPATH','')}
    with (output/'backend.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen([sys.executable,'-u','-m','desk_companion.local_api.server','--port',str(port),'--token',token],cwd=ROOT,env=environment,stdout=log,stderr=log)
        try:
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                if process.poll() is not None:
                    raise RuntimeError('隔离后端启动失败，请查看 backend.log。')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=.5):break
                except OSError:
                    time.sleep(.1)
            else:
                raise TimeoutError('隔离后端未就绪。')
            result=asyncio.run(verify(port,token,model.server_port))
            (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
            print(json.dumps(result,ensure_ascii=False))
            if args.serve_seconds:
                access=output/'access.json'
                access.write_text(json.dumps(dict(port=port,token=token)),'utf-8')
                try:
                    time.sleep(args.serve_seconds)
                finally:
                    access.unlink(missing_ok=True)
        finally:
            try:
                request=urllib.request.Request(f'http://127.0.0.1:{port}/shutdown',headers={'Authorization':'Bearer '+token})
                with urllib.request.urlopen(request,timeout=2):pass
                process.wait(timeout=15)
            except (OSError,subprocess.TimeoutExpired):
                process.terminate();process.wait(timeout=10)
            model.shutdown();model.server_close()


if __name__=='__main__':
    main()
