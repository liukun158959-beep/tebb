"""Bounded cold-worker benchmark. --live really calls the configured model.

Default local SSE fixture tests application overhead, never model performance.
Reports include failures/timeouts; no retries or answer cache. P99 needs >=100.
"""
import argparse
import json
from pathlib import Path
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


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
    parser.add_argument('--output-limit-bindings',type=Path,help='Explicit verified generation-binding SQLite snapshot for this isolated live cohort')
    parser.add_argument('--prewarm',action='store_true',help='预热一次性工作进程，预热成本单独报告')
    parser.add_argument('--python',type=Path,help='使用指定发布 Python 执行当前源码；仅切换工作进程解释器')
    parser.add_argument('--think-time',type=float,default=0,help='每个请求结束后的间隔秒数，不计入请求耗时')
    args=parser.parse_args()
    if not 1<=args.samples<=1000 or not 1<=args.parallel<=4 or not 0<=args.think_time<=30:parser.error('samples 1..1000, parallel 1..4, think-time 0..30')
    if args.output.exists()or args.output.with_name(args.output.stem+'-evidence').exists():parser.error('output and evidence directory must be new')
    if args.output_limit_bindings and(not args.live or not args.output_limit_bindings.is_file()):parser.error('output-limit-bindings requires live and an existing verified snapshot')
    from desk_companion.model_catalog import require_active
    from desk_companion.model_network import load
    from desk_companion.model_capacity import load as load_capacity
    from desk_companion.benchmarking import BenchmarkPlan,run_benchmark
    cfg=require_active()if args.live else dict(id='fixture',api_key='fixture-key',model='local-sse-fixture',base_url='')
    policy=load();policy['mode']=policy['mode']if args.mode=='configured'else args.mode
    capacity=load_capacity(cfg['id'])if args.live else{}
    server=None
    try:
        if not args.live:
            server=ThreadingHTTPServer(('127.0.0.1',0),Fixture);server.daemon_threads=True
            threading.Thread(target=server.serve_forever,daemon=True).start()
            cfg['base_url']=f'http://127.0.0.1:{server.server_port}/v1';policy['mode']='direct'
        plan=BenchmarkPlan(output=args.output,samples=args.samples,parallel=args.parallel,live=args.live,prewarm=args.prewarm,
            think_time=args.think_time,python=args.python,generation_bindings=args.output_limit_bindings)
        report=run_benchmark(plan,cfg,policy,capacity=capacity)
    finally:
        if server:server.shutdown();server.server_close()
    print(json.dumps({k:report[k]for k in('source','run_state','model','summary','checks','passed','evidence_directory','cleanup')},ensure_ascii=False),flush=True)
    if report['run_state']!='completed':raise SystemExit(130 if report['run_state']=='interrupted'else 1)
    if args.check and not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
