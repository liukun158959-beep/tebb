"""Bounded cold-worker benchmark. --live really calls the configured model.

Default local SSE fixture tests application overhead, never model performance.
Reports include failures/timeouts; no retries or answer cache. P99 needs >=100.
"""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desk_companion.performance import summarize,task_timing


class Fixture(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args): pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert body['stream'] and body['messages']
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Connection','close');self.end_headers()
        time.sleep(.015)
        chunks=[dict(choices=[dict(index=0,delta=dict(content='fixture response'),finish_reason='stop')]),dict(choices=[],usage=dict(prompt_tokens=1,completion_tokens=1))]
        self.wfile.write((''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n').encode());self.wfile.flush()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples',type=int,default=100)
    parser.add_argument('--parallel',type=int,default=2)
    parser.add_argument('--live',action='store_true',help='真实模型请求，会产生用量')
    parser.add_argument('--mode',choices=['configured','system','direct'],default='configured')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--prewarm',action='store_true',help='预热一次性工作进程，预热成本单独报告')
    parser.add_argument('--python',type=Path,help='使用指定发布 Python 执行当前源码；仅切换工作进程解释器')
    parser.add_argument('--think-time',type=float,default=0,help='每个请求结束后的间隔秒数，不计入请求耗时')
    args=parser.parse_args()
    if not 1<=args.samples<=1000 or not 1<=args.parallel<=4 or not 0<=args.think_time<=30:parser.error('samples 1..1000, parallel 1..4, think-time 0..30')
    from desk_companion.model_catalog import require_active
    from desk_companion.model_network import load
    cfg=require_active() if args.live else dict(id='fixture',api_key='fixture-key',model='local-sse-fixture',base_url='')
    policy=load();policy['mode']=policy['mode'] if args.mode=='configured' else args.mode
    server=None
    if not args.live:
        server=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        cfg['base_url']=f'http://127.0.0.1:{server.server_port}/v1';policy['mode']='direct'
    original=os.environ.get('DESK_COMPANION_DATA_DIR')
    results=[]; started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='tebb-perf-') as folder:
        root=Path(folder);os.environ['DESK_COMPANION_DATA_DIR']=folder
        (root/'models.json').write_text(json.dumps(dict(active=cfg['id'],items=[cfg])),'utf-8')
        (root/'model-network.json').write_text(json.dumps(policy),'utf-8')
        workspace=root/'workspace.sqlite3'
        with closing(sqlite3.connect(workspace)) as db:
            with db:
                db.execute('CREATE TABLE workspace(id INTEGER PRIMARY KEY,data TEXT)');db.execute('INSERT INTO workspace VALUES(1,?)',(json.dumps(dict(runs=[])),))
        from desk_companion.tasks import TaskManager
        from desk_companion.agent_debug import listing,load_timing
        warm_start=time.monotonic()
        command=None
        if args.python:
            bootstrap=f"import sys;sys.path.insert(0,{str(Path(__file__).resolve().parents[1])!r});from desk_companion.task_worker import main;main()"
            command=[str(args.python.resolve()),'-u','-c',bootstrap]
        manager=TaskManager(path=root/'memory/tasks.sqlite3',prewarm=args.parallel if args.prewarm else 0,command=command);manager.settings['parallel']=args.parallel
        if args.prewarm and not manager.standby.wait_ready(30):manager.shutdown();raise RuntimeError('预热未就绪，未运行基准')
        warmup_sec=round(time.monotonic()-warm_start,2)
        def sample(index):
            task_id=manager.submit('hello，请只用一句短话回应。','bench-'+str(index),'collaboration',workflow='collaboration',kind='chat',
                role=dict(id='tebb',name='tebb',role='直接自然地回复用户',tools=[]),context=[],members=[],model_id=cfg['id'],
                collab_run='bench-'+str(index),collab_path=str(workspace),task_limits=dict(call_timeout=40,task_timeout=70))
            task=manager.wait(task_id);events=task['events'];chain=task_timing(task)
            first=next((e['ts'] for e in events if e['kind']=='token' and e['data']),None)
            calls=sum(e['kind']=='llm_start' for e in events)
            result=dict(index=index,state=task['state'],duration_ms=round((task['ended']-task['created'])*1000,2),
                        first_text_ms=round((first-task['created'])*1000,2) if first else None,
                        worker_prepare_ms=sum(s['duration_ms'] for s in chain['stages'] if s['kind']=='prepare'),
                        model_calls=calls,prewarmed=any(e['kind']=='worker_pool' and e['data']['prewarmed'] for e in events),
                        error='Timeout' if task['state']=='timed_out' else next((e['data'].get('type','') for e in events if e['kind']=='failure' and isinstance(e['data'],dict)),''),
                        longest=chain['longest']['label'] if chain['longest'] else None)
            if task['state']=='succeeded' and (calls!=1 or not task['answer'].strip()):result.update(state='failed',error='InvalidModelReceipt')
            rows=listing(task_id=task_id)['items']
            result['http']=[load_timing(r['id']) for r in rows]
            if args.think_time:time.sleep(args.think_time)
            return result
        try:
            with concurrent.futures.ThreadPoolExecutor(args.parallel) as pool:
                pending={pool.submit(sample,i) for i in range(args.parallel if args.samples>=args.parallel else args.samples)}
                next_index=len(pending)
                while pending:
                    done,pending=concurrent.futures.wait(pending,return_when=concurrent.futures.FIRST_COMPLETED)
                    for future in done:
                        results.append(future.result())
                        if next_index<args.samples:pending.add(pool.submit(sample,next_index));next_index+=1
                    print(json.dumps(dict(completed=len(results),samples=args.samples,failed=sum(r['state']!='succeeded' for r in results))),flush=True)
        finally:
            manager.shutdown()
            if original is None:os.environ.pop('DESK_COMPANION_DATA_DIR',None)
            else:os.environ['DESK_COMPANION_DATA_DIR']=original
            if server:server.shutdown();server.server_close()
    stats=summarize(results)
    limits=dict(duration_ms=30000 if args.live else 5000,first_text_ms=15000 if args.live else 4500,worker_prepare_ms=3500)
    checks={k:stats[k]['p99'] is not None and stats[k]['p99']<=limit for k,limit in limits.items()}
    checks['no_failures']=stats['failed']==0 and stats['completed']==args.samples
    report=dict(source=('live_model' if args.live else 'local_sse_fixture')+('_prewarmed_workers' if args.prewarm else '_cold_workers'),model=cfg['model'],network_mode=policy['mode'],
                parallel=args.parallel,prewarm=args.prewarm,think_time=args.think_time,warmup_sec=warmup_sec,
                prewarmed_samples=sum(s.get('prewarmed',False) for s in results),wall_sec=round(time.monotonic()-started,2),summary=stats,limits_ms=limits,checks=checks,
                passed=all(checks.values()),samples=sorted(results,key=lambda r:r['index']),
                scope='One request per isolated worker and real task queue; excludes UI rendering. Fixture results do not measure a real model. Closed-loop concurrency; optional standby warmup is reported separately; optional think-time is outside request latency.')
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='samples'},ensure_ascii=False),flush=True)
    if args.check and not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
