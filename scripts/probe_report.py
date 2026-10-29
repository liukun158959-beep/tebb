"""Explicit live read-only worker smoke test; never replays a saved user task."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desk_companion import model_catalog,model_network,report_charts,agent_debug
from desk_companion.collaboration_worker import execute


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');parser.add_argument('--url',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    if not args.live:parser.error('--live is required; public reads and paid model calls are performed.')
    model=model_catalog.require_active();policy=model_network.load();events=[];report={};root=uuid.uuid4().hex
    goal=('只读核对这篇官方长文 '+args.url+' 。实际读取正文；若truncated，用同一snapshot_id和next_offset继续读取尾部。'
          '构建一张bar图，比较正文总字符与首次读取字符数，单位为字符，使用读取回执的值、已读正文来源ID，口径明确为采集覆盖统计，不是模型性能。'
          '最后写三句话说明与限制，并原样插入工具返回的图表markdown和正文来源引用；不执行其他业务操作。')
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    previous=os.environ.get('DESK_COMPANION_DATA_DIR')
    try:
        with tempfile.TemporaryDirectory() as folder:
            os.environ['DESK_COMPANION_DATA_DIR']=folder
            Path(folder,'model-network.json').write_text(json.dumps(policy),'utf-8')
            agent_debug.install(dict(session='collab-'+root,model_id=model['id'],collab_run=root,phase='task'))
            request=dict(collab_path=folder+'/isolated',collab_run=root,source_scope=root,kind='task',text=goal,objective=goal,
                context=[dict(sender='user',text=goal)],role=dict(id='research',name='研究助手',role='只读核对和数据呈现',tools=['网页读取','构建图表'],memory=''),
                model_id=model['id'],limits=dict(call_timeout=60,task_timeout=300),sampling=dict(reasoning_effort='low',temperature=.3,top_p=1),
                budget=8,fetch_budget=3,search_budget=0,required_sources=['web'],requires_charts=True,applied=0)
            try:
                with patch.object(model_catalog,'require_item',return_value=model),patch('desk_companion.collaboration_worker.updates',return_value=[]):
                    execute(request,lambda k,d:events.append((k,d)))
                report['ok']=True
            except Exception as exc:report.update(ok=False,error=type(exc).__name__+': '+str(exc).replace(model['api_key'],'[redacted]'))
            answer=next((d for k,d in events if k=='result'),'')
            report.update(model=model['model'],calls=sum(k=='llm_start' for k,d in events),result=answer,
                tools=[d for k,d in events if k in {'tool_start','tool_end'}],citations=[d for k,d in events if k=='citation_check'],
                pages=[{key:d.get(key) for key in ('url','snapshot_id','read_start','read_end','next_offset','characters','sha256')} for k,d in events if k=='evidence'])
            report['charts']=[]
            for ident in report_charts.referenced(answer):
                meta,_=report_charts.load(ident);report['charts'].append(meta)
                for ext in ('png','svg'):shutil.copyfile(report_charts.folder()/(ident+'.'+ext),output.parent/(ident+'.'+ext))
            output.write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    finally:
        if previous is None:os.environ.pop('DESK_COMPANION_DATA_DIR',None)
        else:os.environ['DESK_COMPANION_DATA_DIR']=previous
    print(json.dumps(dict(ok=report.get('ok'),calls=report.get('calls'),pages=len(report.get('pages',[])),charts=len(report.get('charts',[]))),ensure_ascii=False))
    return 0 if report.get('ok') else 1


if __name__=='__main__':sys.exit(main())
