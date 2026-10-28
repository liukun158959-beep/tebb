"""Explicit work-item execution: one transaction owns item, run and receipt."""
import hashlib
import json
from . import work_items
from .collaboration import ACTIVE

class WorkExecutionService:
    def __init__(self, board, collaboration):
        self.board,self.collaboration=board,collaboration

    def execute(self, action):
        attempted=False;receipt=None
        try:
            rid=self.board.validate_request_id(action.get('request_id'))
            fingerprint=hashlib.sha256(json.dumps(action,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            with self.board.hold_item(action.get('id')), self.collaboration.lock:
                with self.board.workspace_transaction() as (state,db):
                    old=db.execute('SELECT fingerprint,result FROM work_receipts WHERE id=?',(rid,)).fetchone()
                    if old:
                        if old[0]!=fingerprint:raise ValueError('操作编号已用于其他事项请求。')
                        receipt=json.loads(old[1])
                    else:
                        item=work_items.require_item(state,action.get('id'))
                        work_items.check_version(item,action.get('version'))
                        if len(item['links'])>=50:raise ValueError('执行历史已达50条，请先解除不需要的关联。')
                        if item['archived']:raise ValueError('请先恢复事项，再交给AI执行。')
                        if any(r.get('source',{}).get('kind')=='work_item' and r['source']['id']==item['id']
                               and (r['state'] in ACTIVE or r['state']=='needs_input' or r.get('delivery_state')=='running') for r in state['runs']):
                            raise ValueError('此事项已有未结束的AI执行，请先完成或停止。')
                        text=item['title']+'\n\n'+item['description']+'\n\n请完成这件事项并报告实际成果和验证；业务进度由用户确认。'
                        run=self.collaboration.create_execution(state,item.get('agent_id','tebb'),text,item['project_id'],
                            dict(kind='work_item',id=item['id'],version=item['version']))
                        item['links'].append(dict(id=run['id'],title=run['title'],conversation=run['conversation'],project_name=item.get('project_name_snapshot','')))
                        work_items.touch_item(item)
                        receipt=dict(request_id=rid,item_id=item['id'],version=item['version'],operation='execute',run_id=run['id'])
                        db.execute('INSERT INTO work_receipts VALUES(?,?,?)',(rid,fingerprint,json.dumps(receipt)))
                    attempted=True
            page=self.board.load(rid)
            if not page['ok']:return dict(ok=False,request_id=rid,receipt=receipt,change_state='committed',stage='refresh',error='AI执行已登记，但页面读取失败；请重新读取，不要重复执行。')
            return page
        except Exception as exc:
            return dict(ok=False,request_id=action.get('request_id'),stage='execute_save' if attempted else 'validate',
                        change_state='unconfirmed' if attempted else 'not_changed',error=str(exc))
