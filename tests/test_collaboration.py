import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from desk_companion.collaboration import Collaboration, parse_plan, transaction
from desk_companion.collaboration_worker import execute

MODEL = dict(id="model-one", model="fixture-model", base_url="https://example.invalid/v1", api_key="fixture-key")


class Queue:
    def __init__(self):
        self.settings = {}; self.items = {}; self.requests = {}

    def submit(self, text, session, channel, submission_key="", **request):
        if submission_key not in self.items:
            self.items[submission_key] = dict(id=submission_key, state="queued", answer="", error="", events=[])
            self.requests[submission_key] = dict(text=text, session_id=session, **request)
        return submission_key

    def get(self, task): return copy.deepcopy(self.items[task])
    def cancel(self, task): self.items[task]["state"] = "cancelled"
    def shutdown(self): pass

    def complete(self, task, answer, applied=0, state="succeeded", evidence=None):
        row = self.items[task]; row.update(state=state, answer=answer, error="测试失败" if state == "failed" else "")
        row["events"] = [dict(seq=1, kind="llm_start", data={}), dict(seq=2, kind="collab_applied", data=applied),
                         dict(seq=3, kind="usage", data=dict(input_tokens=10, output_tokens=5))]
        if evidence: row["events"].append(dict(seq=4, kind="evidence", data=evidence))


class CollaborationTests(unittest.TestCase):
    def test_partner_memory_updates_only_member_memory_and_preserves_configuration(self):
        before=copy.deepcopy(next(a for a in self.state()['agents'] if a['id']=='research'))
        started=self.service.action(dict(type='send',conversation='dm-research',text='核对资料来源',mode='auto',request_id=uuid.uuid4().hex))['state']['runs'][-1]
        state=self.action(type='partner_memory',agent_id='research',memory='只改记忆')
        after=next(a for a in state['agents'] if a['id']=='research')
        self.assertEqual(after,{**before,'memory':'只改记忆'})
        with transaction(self.service.path) as (internal,_):
            self.assertEqual(next(r for r in internal['runs'] if r['id']==started['id'])['agent_config']['memory'],before['memory'])
        with self.assertRaises(ValueError):self.service.action(dict(type='partner_memory',conversation='dm-writer',agent_id='research',memory='不应写入',request_id=uuid.uuid4().hex))
        with self.assertRaises(ValueError):self.action(type='partner_memory',agent_id='research',memory='x'*2001)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"DESK_COMPANION_DATA_DIR": self.temp.name}); self.env.start()
        self.model = patch("desk_companion.model_catalog.require_active", return_value=MODEL); self.model.start()
        self.item = patch("desk_companion.model_catalog.require_item", return_value=MODEL); self.item.start()
        self.work, self.chat = Queue(), Queue()
        self.service = Collaboration(path=Path(self.temp.name)/"workspace.sqlite3", managers=dict(work=self.work, chat=self.chat), start=False)

    def tearDown(self):
        self.service.shutdown(); self.item.stop(); self.model.stop(); self.env.stop(); self.temp.cleanup()

    def state(self): return self.service.snapshot()["state"]

    def action(self, **values):
        result=self.service.action(dict(request_id=uuid.uuid4().hex, conversation="group-research", **values))["state"]
        # Execution-layer fixtures begin after readiness. End-to-end readiness and
        # the same-root transition are covered separately in test_execution_flow.
        if values.get('type')=='send' and values.get('mode')=='task' and not values.get('run'):
            with transaction(self.service.path) as (state,_):
                run=next(r for r in state['runs'] if r['id']==result['runs'][-1]['id'])
                run['kind']='plan' if run['agent']=='tebb' and run.get('members') else 'task'
            result=self.state()
        return result

    def plan(self, dependency=0):
        return json.dumps({"tasks": [dict(agent_id="research", title="研究", instructions="根据提供材料研究", depends_on=None),
            dict(agent_id="writer", title="报告", instructions="根据研究成果写报告", depends_on=dependency)]})

    def test_route_public_tokens_and_tool_progress_reach_chat_before_completion(self):
        state=self.action(type='send',text='请比较这两种算法')
        run=state['runs'][-1];self.service.tick();run=self.state()['runs'][-1]
        task=self.chat.items[run['task']] if 'task' in run else next(iter(self.chat.items.values()))
        task.update(state='running',answer='第一段')
        task['events']=[dict(seq=1,kind='token',data='第一段'),dict(seq=2,kind='status',data='正在思考与组织回答。')]
        self.service.tick();state=self.state()
        self.assertEqual(state['messages'][-1]['text'],'第一段')
        self.assertEqual(state['runs'][-1]['status'],'正在思考与组织回答。')
        task.update(state='succeeded',answer=json.dumps(dict(mode='task',instructions='control only')))
        self.service.tick();state=self.state()
        route=next(r for r in state['runs'] if r['id']==run['id'])
        self.assertEqual(route['answer'],'');self.assertIsNone(route['parent'])
        self.assertEqual(route['kind'],'task');self.assertEqual(len(state['runs']),1)
        self.assertNotIn('已开始处理，任务进度会持续更新。',str(state['messages']))

    def test_public_snapshot_delta_updates_orders_deletions_and_redacts_internal_data(self):
        before=self.service.snapshot();self.action(type='send',text='hello')
        update=self.service.snapshot(since=before['sync'])
        self.assertNotIn('state',update)
        merged=copy.deepcopy(before['state']);merged.update(update['patch']['values'])
        for key,delta in update['patch']['collections'].items():
            rows={row['id']:row for row in merged[key]};rows.update({row['id']:row for row in delta['upsert']})
            merged[key]=[rows[ident] for ident in delta['order']]
        self.assertEqual(merged,self.state())
        self.assertNotIn('agent_config',str(update));self.assertNotIn('fixture-key',str(update))
        unchanged=self.service.snapshot(since=update['sync'])
        self.assertEqual(unchanged['patch']['collections'],{});self.assertEqual(unchanged['patch']['values'],{})
        self.action(type='delete_conversation',id='dm-writer')
        removed=self.service.snapshot(since=update['sync'])
        self.assertNotIn('dm-writer',removed['patch']['collections']['conversations']['order'])
        self.assertIn('state',self.service.snapshot(since='expired'))

    def test_plain_greeting_streams_with_history_but_actions_still_route(self):
        state=self.action(type='send',text='hello!')
        self.assertEqual(state['runs'][-1]['kind'],'chat')
        self.service.tick()
        self.assertEqual(next(iter(self.chat.requests.values()))['kind'],'chat')
        state=self.action(type='send',text='hello!')
        self.assertEqual(state['runs'][-1]['kind'],'chat')
        self.service.tick()
        self.assertTrue(list(self.chat.requests.values())[-1]['context'])
        for text in ('继续上次任务', 'hello，帮我搜新闻', '@阿探 hello', 'hi; 删除文件'):
            self.assertEqual(self.action(type='send',text=text)['runs'][-1]['kind'],'route')

    def test_companion_migration_preserves_custom_configuration_and_backs_up(self):
        with transaction(self.service.path) as (state, _):
            state.pop("companion_version")
            state["agents"][0]["name"] = "tebb"
            state["agents"][1].update(name="自定义伙伴", memory="KEEP_PRIVATE", model="custom-model", role="自定义职责")
            state["agents"][2]["name"] = "写作者"
            state["agents"][2].pop("personality")
        reopened = Collaboration(path=self.service.path, managers=self.service.managers, start=False)
        try:
            agents = reopened.snapshot()["state"]["agents"]
            self.assertEqual([a["name"] for a in agents], ["tebb · 通用助手", "自定义伙伴", "写作助手", "图表助手"])
            self.assertEqual(agents[1]["memory"], "KEEP_PRIVATE")
            self.assertEqual(agents[1]["model"], "custom-model")
            self.assertEqual(agents[1]["role"], "自定义职责")
            self.assertEqual(agents[2]["avatar"], "cat")
            self.assertTrue(self.service.path.with_name("workspace.before-companions.sqlite3").exists())
            self.action(type="agent", agent={**agents[2], "name": "写作者"})
            again = Collaboration(path=self.service.path, managers=self.service.managers, start=False)
            self.assertEqual(again.snapshot()["state"]["agents"][2]["name"], "写作者")
            again.shutdown()
        finally:
            reopened.shutdown()

    def test_delete_requires_terminal_work_and_is_scoped_and_idempotent(self):
        self.action(type="send", text="不能删除进行中的任务", target="research", mode="task")
        run = self.state()["runs"][0]
        with self.assertRaisesRegex(ValueError, "先停止"):
            self.action(type="delete_conversation", id="group-research")
        self.action(type="cancel", id=run["id"])
        self.service.action(dict(type="send", conversation="dm-research", text="独立会话保留", target="research", mode="message", request_id=uuid.uuid4().hex))
        action = dict(type="delete_conversation", id="group-research", request_id="delete-test-001")
        self.service.action(action); self.service.action(action)
        state = self.state()
        self.assertFalse(any(c["id"] == "group-research" for c in state["conversations"]))
        self.assertFalse(any(r["conversation"] == "group-research" for r in state["runs"]))
        self.assertTrue(any(m["text"] == "独立会话保留" for m in state["messages"]))
        self.assertIn(state["selected"], [c["id"] for c in state["conversations"]])
        self.assertEqual(len(state["agents"]), 4)
        with self.assertRaisesRegex(ValueError, "会话不存在"):
            self.action(type="send", text="过期入口不能重建旧对话", target="", mode="message")

    def test_deleted_dm_reopens_empty_with_private_memory_and_new_identity(self):
        agent = self.state()["agents"][1]
        self.action(type="agent", agent={**agent, "memory": "PRIVATE_KEEP", "avatar": "owl", "personality": "好奇温柔"})
        self.action(type="delete_conversation", id="dm-research")
        state = self.action(type="open_dm", agent_id="research")
        self.assertNotEqual(state["selected"], "dm-research")
        self.assertFalse(any(m["conversation"] == state["selected"] for m in state["messages"]))
        self.assertEqual(state["agents"][1]["memory"], "PRIVATE_KEEP")
        self.service.action(dict(type="send", conversation=state["selected"], text="你好", target="", mode="message", request_id=uuid.uuid4().hex))
        self.service.tick()
        request = next(iter(self.chat.requests.values()))
        self.assertEqual(request["role"]["personality"], "好奇温柔")
        self.assertEqual(request["role"]["avatar"], "owl")

    def test_last_conversation_delete_always_leaves_a_valid_empty_chat(self):
        for c in list(self.state()["conversations"]):
            self.action(type="delete_conversation", id=c["id"])
        state = self.state()
        self.assertEqual(len(state["conversations"]), 1)
        self.assertEqual(state["selected"], state["conversations"][0]["id"])
        self.assertEqual(state["messages"], [])

    def test_group_rename_and_avatar_validation(self):
        state = self.action(type="rename_conversation", id="group-research", title="灵感工作室")
        self.assertEqual(state["conversations"][0]["title"], "灵感工作室")
        with self.assertRaises(ValueError):
            self.action(type="rename_conversation", id="dm-research", title="不能改成群名")
        for avatar in ("https://tracking.invalid/a.svg", [], None):
            with self.assertRaisesRegex(ValueError, "头像无效"):
                self.action(type="agent", agent={**state["agents"][0], "avatar": avatar})

    def test_projects_migrate_old_history_and_persist_without_changing_selection(self):
        before = self.state()
        with transaction(self.service.path) as (state, _): state.pop('projects', None)
        migrated = self.state()
        self.assertEqual(migrated['projects'], [])
        self.assertEqual(migrated['conversations'], before['conversations'])
        request = dict(type='create_project', name=' Atlas ', request_id='project-create-001')
        self.service.action(request); self.service.action(request)
        state = self.state(); project = state['projects'][0]
        self.assertEqual(len(state['projects']), 1)
        self.assertEqual(project['name'], 'Atlas')
        self.action(type='move_conversation', id='group-research', project_id=project['id'])
        self.action(type='rename_project', id=project['id'], name='Atlas 开发')
        reopened = Collaboration(path=self.service.path, managers=self.service.managers, start=False)
        try:
            saved = reopened.snapshot()['state']
            self.assertEqual(saved['projects'], [dict(id=project['id'], name='Atlas 开发', primary='')])
            self.assertEqual(saved['selected'], before['selected'])
            self.assertEqual(saved['conversations'][0]['project_id'], project['id'])
        finally: reopened.shutdown()

    def test_concurrent_selection_cannot_replace_the_snapshot_returned_for_an_action(self):
        import time
        ready, attempted=threading.Event(),threading.Event();original=self.service.snapshot;result={}
        def snapshot():
            if threading.current_thread().name=='first-selection':
                ready.set();self.assertTrue(attempted.wait(2));time.sleep(.04)
            return original()
        def first():result['first']=self.service.action(dict(type='select',conversation='dm-research',request_id=uuid.uuid4().hex))['state']
        def second():
            ready.wait(2);attempted.set();result['second']=self.service.action(dict(type='select',conversation='dm-writer',request_id=uuid.uuid4().hex))['state']
        with patch.object(self.service,'snapshot',side_effect=snapshot):
            a=threading.Thread(target=first,name='first-selection');b=threading.Thread(target=second,name='second-selection');a.start();b.start();a.join(3);b.join(3)
        self.assertFalse(a.is_alive() or b.is_alive())
        self.assertEqual(result['first']['selected'],'dm-research');self.assertEqual(result['second']['selected'],'dm-writer')

    def test_project_validation_rolls_back_missing_targets_and_duplicate_names(self):
        for name in ('', '  ', None, [], 'x'*41):
            with self.assertRaises(ValueError): self.action(type='create_project', name=name)
        first = self.action(type='create_project', name='Atlas')['projects'][0]
        second = self.action(type='create_project', name='研究')['projects'][1]
        for kind in ('create_project', 'rename_project'):
            with self.assertRaisesRegex(ValueError, '已存在'): self.action(type=kind, id=second['id'], name='ATLAS')
        before = self.state()
        for kind in ('move_conversation', 'new_chat', 'group', 'open_dm'):
            with self.assertRaises(ValueError): self.action(type=kind, id='group-research' if kind=='move_conversation' else None, project_id='missing', agent_id='tebb', title='测试群', members=['tebb','writer'])
        self.assertEqual(self.state()['conversations'], before['conversations'])
        for kind in ('rename_project','delete_project'):
            with self.assertRaisesRegex(ValueError, '不存在'): self.action(type=kind, id='missing', name='测试')
        with self.assertRaises(ValueError): self.action(type='move_conversation', id='group-research', project_id=None)
        self.assertEqual(self.state()['projects'][0], first)

    def test_project_chats_groups_and_partner_entry_keep_their_project(self):
        project = self.action(type='create_project', name='Atlas')['projects'][0]['id']
        state = self.action(type='new_chat', project_id=project)
        dm = next(c for c in state['conversations'] if c['id']==state['selected'])
        self.assertEqual(dm['project_id'], project)
        state = self.service.action(dict(type='new_chat', conversation=dm['id'], request_id=uuid.uuid4().hex))['state']
        self.assertEqual(state['conversations'][-1]['project_id'], project)
        state = self.action(type='group', title='源码研究', members=['research','writer'], goal='核对源码', project_id=project)
        group_id = state['selected']
        state = self.action(type='group', id=group_id, title='修改标题', members=['research','writer'], goal='补充目标')
        self.assertEqual(next(c for c in state['conversations'] if c['id']==group_id)['project_id'], project)
        state = self.action(type='open_dm', agent_id='research', project_id=project)
        self.assertNotEqual(state['selected'], 'dm-research')
        project_dm = state['selected']
        self.assertEqual(next(c for c in state['conversations'] if c['id']==project_dm)['project_id'], project)
        self.assertEqual(self.action(type='open_dm', agent_id='research', project_id=project)['selected'], project_dm)
        state = self.action(type='new_chat', project_id='')
        self.assertEqual(state['conversations'][-1]['project_id'], '')

    def test_folder_projects_reuse_paths_keep_separate_grants_and_pin_running_workspace(self):
        from desk_companion import project_workspace as project
        first=Path(self.temp.name)/'a'/'app';first.mkdir(parents=True);(first/'who.txt').write_text('A')
        other=Path(self.temp.name)/'b'/'app';other.mkdir(parents=True);(other/'who.txt').write_text('B')
        state=self.action(type='open_folder_project',primary=str(first));pid=state['projects'][0]['id'];cid=state['selected']
        cfg=project.save(str(first),edit=True,project_id=pid)['workspace']
        state=self.action(type='open_folder_project',primary=str(other));other_pid=state['conversations'][-1]['project_id']
        self.assertNotEqual(pid,other_pid);self.assertEqual(len({p['name'] for p in state['projects']}),2)
        self.assertFalse(state['workspace']['edit']);self.assertFalse(state['workspace']['commands'])
        self.assertEqual(project.current(cfg),cfg)
        read=project.read('main','who.txt',config=cfg);self.assertEqual(read['text'],'A')
        project.write('who.txt','A changed',read['revision'],config=cfg)
        self.assertEqual((other/'who.txt').read_text(),'B')
        self.service.action(dict(type='send',conversation=cid,text='继续项目A',mode='message',request_id=uuid.uuid4().hex))
        with transaction(self.service.path) as (internal,_): self.assertEqual(internal['runs'][-1]['workspace'],cfg)
        state=self.action(type='open_folder_project',primary=str(first))
        self.assertEqual(len(state['projects']),2);self.assertEqual(state['workspace'],cfg)
        project.save(str(first),edit=False,project_id=pid)
        with self.assertRaisesRegex(ValueError,'授权已变化'):project.current(cfg)

    def test_unbound_projects_can_attach_folders_and_share_only_explicit_materials(self):
        from desk_companion.bridge import Bridge
        from types import SimpleNamespace
        folder=Path(self.temp.name)/'folder';folder.mkdir()
        state=self.action(type='create_project',name='保留项目名');pid=state['projects'][0]['id']
        self.action(type='move_conversation',id='group-research',project_id=pid)
        state=self.action(type='open_folder_project',primary=str(folder),bind_id=pid)
        self.assertEqual(state['projects'][0]['name'],'保留项目名');self.assertEqual(state['selected'],'group-research')
        self.action(type='rename_project',id=pid,name='保留项目名',instructions='所有结论都应给出来源')
        self.action(type='send',text='产出共享成果',mode='message')
        with transaction(self.service.path) as (state,_):
            run=state['runs'][-1];run.update(state='succeeded',answer='明确保存的结论');run_id=run['id']
            state['messages'].append(dict(id=uuid.uuid4().hex,conversation='group-research',sender='user',text='其他聊天的私有原文'))
        bridge=Bridge(SimpleNamespace(collaboration=self.service))
        with patch('desk_companion.notebook.save_agent_artifact',return_value={'ok':True}):
            bridge.save_collaboration_note(run_id);bridge.save_collaboration_note(run_id)
        self.assertEqual(len(self.state()['projects'][0]['sources']),1)
        from desk_companion.chat_projects import search_chats
        state=self.action(type='new_chat',project_id=pid);cid=state['selected']
        self.service.action(dict(type='send',conversation=cid,text='继续',mode='message',request_id=uuid.uuid4().hex))
        with transaction(self.service.path) as (state,_): context=json.dumps(state['runs'][-1]['context'],ensure_ascii=False)
        self.assertIn('所有结论都应给出来源',context);self.assertIn('明确保存的结论',context);self.assertNotIn('其他聊天的私有原文',context)
        state=self.action(type='new_chat',project_id='');unbound=state['selected']
        self.service.action(dict(type='send',conversation=unbound,text='独立聊天',mode='message',request_id=uuid.uuid4().hex))
        with transaction(self.service.path) as (state,_):
            self.assertFalse(state['runs'][-1]['workspace']['primary'])
            self.assertNotIn('明确保存的结论',json.dumps(state['runs'][-1]['context'],ensure_ascii=False))
            state['messages'].append(dict(id=uuid.uuid4().hex,conversation=unbound,sender='user',text='其他聊天的私有原文 OTHER_PROJECT'))
            current_run=next(r['id'] for r in state['runs'] if r['conversation']==cid)
        matches=search_chats(self.service.path,pid,current_run,'私有原文')['matches']
        self.assertEqual(len(matches),1);self.assertNotIn('OTHER_PROJECT',matches[0]['text'])
        self.assertEqual(matches[0]['conversation'],'group-research')

    def test_history_folder_preview_is_readonly_and_migration_requires_current_explicit_selection(self):
        from desk_companion.chat_projects import preview
        from desk_companion import project_workspace as project
        folder=Path(self.temp.name)/'history';folder.mkdir();(folder/'keep.txt').write_text('unchanged')
        second=Path(self.temp.name)/'second';second.mkdir()
        with transaction(self.service.path) as (state,_):
            for cid,paths,status in [('dm-research',[str(folder)],'succeeded'),('dm-writer',[str(folder),str(second)],'succeeded'),('dm-tebb',[str(folder)],'pending')]:
                for primary in paths:state['runs'].append(dict(id=uuid.uuid4().hex,conversation=cid,state=status,workspace=dict(primary=primary)))
            raw=copy.deepcopy(state)
            result=preview(state);self.assertEqual(state,raw)
        rows={r['id']:r for r in result['rows']}
        self.assertEqual(rows['dm-research']['status'],'matched');self.assertEqual(rows['dm-writer']['status'],'multiple');self.assertEqual(rows['dm-tebb']['status'],'busy');self.assertEqual(rows['group-research']['status'],'none')
        self.assertEqual(self.state()['projects'],[])
        for ids,token in [(['dm-writer'],result['token']),(['dm-tebb'],result['token']),(['dm-research'],'stale'),([[]],result['token'])]:
            with self.assertRaises(ValueError):self.action(type='migrate_projects',ids=ids,token=token)
        state=self.action(type='migrate_projects',ids=['dm-research'],token=result['token'])
        pid=next(c for c in state['conversations'] if c['id']=='dm-research')['project_id']
        self.assertEqual(project.load(pid)['primary'],str(folder));self.assertFalse(project.load(pid)['edit'])
        self.assertEqual((folder/'keep.txt').read_text(),'unchanged')
        self.assertNotIn('project_id',next(c for c in state['conversations'] if c['id']=='dm-writer'))
        with self.assertRaisesRegex(ValueError,'重新预览'):self.action(type='migrate_projects',ids=['dm-research'],token=result['token'])

    def test_moving_and_deleting_a_project_preserve_live_work_messages_and_permissions(self):
        project = self.action(type='create_project', name='研究')['projects'][0]['id']
        self.action(type='move_conversation', id='group-research', project_id=project)
        self.action(type='send', text='保留这个任务', target='research', mode='task')
        before = self.state()
        moved = self.action(type='move_conversation', id='group-research', project_id=project)
        self.assertEqual(moved['messages'], before['messages'])
        self.assertEqual(moved['runs'], before['runs'])
        self.assertEqual(moved['agents'], before['agents'])
        self.assertEqual(moved['workspace'], before['workspace'])
        with self.assertRaisesRegex(ValueError,'等待或停止'): self.action(type='move_conversation',id='group-research',project_id='')
        deleted = self.action(type='delete_project', id=project)
        self.assertEqual(deleted['projects'], [])
        self.assertEqual(deleted['messages'], before['messages'])
        self.assertEqual(deleted['runs'], before['runs'])
        self.assertEqual(deleted['selected'], before['selected'])
        self.assertFalse(deleted['conversations'][0].get('project_id'))
        self.service.tick()
        self.assertEqual(len(self.work.items), 1)

    def test_auto_answer_stays_chat_and_auto_task_hands_off_without_manual_mode(self):
        self.action(type='send', text='介绍一下你能做什么', mode='auto', target='')
        self.service.tick(); root = self.state()['runs'][-1]
        self.assertEqual(root['kind'],'route'); self.assertEqual(len(self.work.items),0)
        self.chat.complete('collab:'+root['id']+':0',json.dumps(dict(mode='answer',text='你好！')))
        self.service.tick(); self.assertEqual(self.state()['runs'][-1]['answer'],'你好！')
        self.assertEqual(len(self.state()['runs']),1)
        self.action(type='send',text='研究并写成报告',mode='auto',target='')
        self.service.tick(); root=self.state()['runs'][-1]
        self.chat.complete('collab:'+root['id']+':0',json.dumps(dict(mode='task',instructions='研究并写成报告')))
        self.service.tick(); child=self.state()['runs'][-1]
        self.assertEqual(child['kind'],'task');self.assertIsNone(child['parent']);self.assertEqual(child['id'],root['id'])
        self.assertIn('研究并写成报告',child['input'] if 'input' in child else child['title'])

    def test_route_accepts_plain_code_in_one_call_without_executing_tools(self):
        request=self.request();request['kind']='route';request['require_execution']=False
        code='```python\ndef bubble_sort(a):\n    pass\n```';calls=[];events=[]
        class LLM:
            def chat(_,messages,tools,on_delta):
                calls.append(copy.deepcopy(messages)); self.assertEqual([t['function']['name'] for t in tools],['start_background_task','request_clarification'])
                return dict(message=dict(content=code if len(calls)==1 else json.dumps(dict(mode='answer',text=code))),usage={})
        execute(request,lambda k,d:events.append((k,d)),llm=LLM())
        self.assertEqual(len(calls),1)
        self.assertEqual(json.loads(events[-1][1])['text'],code)
        self.assertFalse(any(k=='tool_start' for k,d in events))
        self.assertFalse(any(k=='status' and '纠正' in d for k,d in events))

    def test_plan_exposes_structured_submission_and_repairs_no_tool_prose_without_execution(self):
        request=self.request();request.update(kind='plan',role=dict(self.request()['role'],id='tebb'),members=[dict(id='research',name='研究助手',role='研究')],budget=2)
        events=[];calls=[]
        plan=dict(tasks=[dict(agent_id='research',title='核对资料',instructions='读取一手资料后核对结论',depends_on=None,stage='collect')])
        class LLM:
            def chat(_,messages,tools,on_delta):
                calls.append(1);self.assertEqual([t['function']['name'] for t in tools],['submit_plan'])
                self.assertIn('禁止委派自己',messages[0]['content'])
                if len(calls)==1:return dict(message=dict(content='当前没有日程工具，无法创建。'),usage={})
                return dict(message=dict(role='assistant',tool_calls=[dict(id='plan',type='function',function=dict(name='submit_plan',arguments=json.dumps(plan)))]),usage={})
        execute(request,lambda k,d:events.append((k,d)),llm=LLM())
        self.assertEqual(json.loads(events[-1][1]),plan);self.assertEqual(len(calls),2)
        self.assertFalse(any(k=='tool_start' for k,d in events))

    def test_invalid_plan_has_bounded_specific_failure_and_no_business_side_effect(self):
        from desk_companion.collaboration_worker import PublicFailure
        request=self.request();request.update(kind='plan',members=[dict(id='tebb')],budget=2);calls=[];events=[]
        class LLM:
            def chat(_,messages,tools,on_delta):
                calls.append(1)
                return dict(message=dict(role='assistant',tool_calls=[dict(id='bad-plan',type='function',function=dict(name='submit_plan',arguments=json.dumps(dict(tasks=[dict(agent_id='missing',title='x',instructions='x')]))))]),usage={})
        with self.assertRaisesRegex(PublicFailure,'委派计划格式纠正后仍无效'):
            execute(request,lambda k,d:events.append((k,d)),llm=LLM())
        self.assertEqual(len(calls),2);self.assertFalse(any(k in {'tool_start','result'} for k,d in events))

    def test_invalid_route_repair_is_bounded_and_cannot_dispatch(self):
        from desk_companion.collaboration_worker import PublicFailure
        request=self.request();request['kind']='route';calls=[];events=[]
        class LLM:
            def chat(_,messages,tools,on_delta):
                calls.append(1);return dict(message=dict(content='{"mode":"wrong"}'),usage={})
        with self.assertRaisesRegex(PublicFailure,'未执行工具'):
            execute(request,lambda k,d:events.append((k,d)),llm=LLM())
        self.assertEqual(len(calls),2)
        self.assertFalse(any(k in {'tool_start','result'} for k,d in events))

    def test_shared_task_limits_apply_to_collaboration_without_rewriting_existing_jobs(self):
        from desk_companion.collaboration import execution_limits
        folder=Path(self.temp.name)/'memory';folder.mkdir(exist_ok=True)
        path=folder/'task_settings.json';path.write_text(json.dumps(dict(call_timeout=150,task_timeout=900)))
        self.assertEqual(execution_limits('coder'),dict(call_timeout=150,task_timeout=900))
        self.action(type='send',text='写代码',mode='task',target='research');self.service.tick()
        saved=next(iter(self.work.requests.values()))['task_limits']
        self.assertEqual(saved,dict(call_timeout=150,task_timeout=900))
        path.write_text(json.dumps(dict(call_timeout=20,task_timeout=40)))
        self.assertEqual(saved,dict(call_timeout=150,task_timeout=900))
        self.assertEqual(execution_limits('research'),dict(call_timeout=20,task_timeout=40))

    def test_route_function_requests_one_background_task_without_running_it(self):
        request=self.request();request['kind']='route';events=[]
        class LLM:
            def chat(_,messages,tools,on_delta):
                return dict(message=dict(tool_calls=[dict(function=dict(name='start_background_task',arguments=json.dumps(dict(instructions='读取app.py并运行测试'))))]),usage={})
        execute(request,lambda k,d:events.append((k,d)),llm=LLM())
        self.assertEqual(json.loads(events[-1][1]),dict(mode='task',instructions='读取app.py并运行测试'))
        self.assertFalse(any(k=='tool_start' for k,d in events))

    def test_duplicate_route_requests_coalesce_into_one_original_goal_without_extra_model_call(self):
        request=self.request();request['kind']='route';events=[];calls=[]
        class LLM:
            def chat(_,messages,tools,on_delta):
                calls.append(1)
                return dict(message=dict(tool_calls=[dict(function=dict(name='start_background_task',arguments=json.dumps(dict(instructions=part)))) for part in ['只查资料','只写报告']]),usage={})
        execute(request,lambda k,d:events.append((k,d)),llm=LLM())
        self.assertEqual(len(calls),1)
        results=[json.loads(d) for k,d in events if k=='result']
        self.assertEqual(results,[dict(mode='task',instructions=request['text'],strategy='direct')])
        self.assertFalse(any(k=='tool_start' for k,d in events))

    def test_chat_artifact_can_be_saved_once_as_note_and_read_as_full_attachment(self):
        from desk_companion.bridge import Bridge
        from desk_companion import notebook, local_sources
        from types import SimpleNamespace
        bridge=Bridge(SimpleNamespace(collaboration=self.service))
        self.action(type='send',text='生成一份说明',mode='message',target='research');self.service.tick()
        run=self.state()['runs'][-1]
        with self.assertRaisesRegex(ValueError,'成功完成'):bridge.save_collaboration_note(run['id'])
        body='成果正文。'*2000
        self.chat.complete('collab:'+run['id']+':0',body);self.service.tick()
        with patch('desk_companion.knowledge.public_view',return_value=dict(docs=[])):
            first=bridge.save_collaboration_note(run['id']);second=bridge.save_collaboration_note(run['id'])
            self.assertEqual(first,second)
            book=notebook.load_notebook();self.assertEqual(len(book['sessions'][0]['notes']),1)
            self.assertIn('AI 生成',book['sessions'][0]['notes'][0]['answer'])
            staged=bridge.prepare_notebook_source(first['session_id'],first['note_id'])
        self.assertEqual(len(staged['sources']),1)
        snapshot=local_sources.get(staged['sources'][0]['id'])
        self.assertIn(body,snapshot['text']);self.assertIn('未经额外来源核验',snapshot['text'])

    def test_multiple_mentions_limit_delegation_and_literal_code_is_not_a_mention(self):
        self.action(type='send',text='@阿探 @墨墨 一起研究',mode='auto',target='')
        self.service.tick(); root=self.state()['runs'][-1]
        self.chat.complete('collab:'+root['id']+':0',json.dumps(dict(mode='answer',text='不能只聊天')))
        self.service.tick()
        with transaction(self.service.path) as (state,_):
            self.assertEqual({m['id'] for m in state['runs'][-1]['members']},{'research','writer'})
        self.action(type='send',text='解释代码：```python\n@dataclass\nclass A: pass\n```',mode='auto',target='')
        with self.assertRaises(ValueError): self.action(type='send',text='@不存在 帮忙',mode='auto',target='')

    def test_browser_capability_cannot_enable_or_execute_local_tools(self):
        with self.assertRaises(ValueError):
            self.service.action(dict(type='add_coder',request_id=uuid.uuid4().hex),readonly=True)
        agent=self.state()['agents'][0]
        with self.assertRaises(ValueError):
            self.service.action(dict(type='agent',agent={**agent,'tools':['文件编辑']},request_id=uuid.uuid4().hex),readonly=True)
        self.action(type='add_coder')
        self.service.action(dict(type='send',conversation='dm-coder',text='写文件',mode='auto',target='',request_id=uuid.uuid4().hex))
        with self.assertRaises(ValueError):
            self.service.action(dict(type='send',conversation='dm-coder',text='写文件',mode='auto',target='',request_id=uuid.uuid4().hex),readonly=True)

    def test_legacy_history_import_is_once_and_continuable_without_changing_original(self):
        from desk_companion.memory import append_chat, memory_path
        append_chat('user','旧对话问题','legacy-one'); append_chat('pet','旧对话回答','legacy-one')
        raw=memory_path().read_bytes()
        with transaction(self.service.path) as (state,_): state.pop('legacy_imported',None)
        reopened=Collaboration(path=self.service.path,managers=self.service.managers,start=False)
        try:
            state=reopened.snapshot()['state']; conversation=next(c for c in state['conversations'] if c.get('legacy'))
            self.assertEqual([m['text'] for m in state['messages'] if m['conversation']==conversation['id']],['旧对话问题','旧对话回答'])
            reopened.action(dict(type='send',conversation=conversation['id'],text='继续',mode='auto',target='',request_id=uuid.uuid4().hex))
            with transaction(self.service.path) as (current,_): self.assertIn('旧对话回答',json.dumps(current['runs'][-1]['context'],ensure_ascii=False))
            self.assertEqual(memory_path().read_bytes(),raw)
        finally: reopened.shutdown()

    def test_coder_real_file_tool_loop_records_failure_fix_and_fresh_verification(self):
        from desk_companion import project_workspace as project
        folder=Path(self.temp.name)/'code';folder.mkdir();(folder/'app.py').write_bytes(b'value = 1\n')
        self.action(type='open_folder_project',primary=str(folder))
        cfg=project.save(str(folder),[],True,True)['workspace']
        request=self.request(role=dict(id='coder',name='码可',role='修改并验证',tools=['工作区读取','文件编辑','终端'],memory=''))
        request.update(workspace=cfg,budget=6)
        calls=[];events=[]
        class LLM:
            def chat(_,messages,tools,on_delta):
                calls.append(messages)
                step=len(calls)
                if step in (1,3,5):
                    name='workspace_run';arguments=dict(command='python -m unittest',timeout=20)
                elif step in (2,4):
                    name='workspace_write';arguments=dict(path='app.py',text='value = 2\n' if step==2 else 'value = 3\n',revision=project.read('main','app.py')['revision'])
                else: return dict(message=dict(content='已修改，验证结果见回执。'),usage={})
                return dict(message=dict(role='assistant',tool_calls=[dict(id=str(step),type='function',function=dict(name=name,arguments=json.dumps(arguments)))]),usage={})
        count=[0]
        def command(*_):
            count[0]+=1
            return dict(command='python -m unittest',state='failed' if count[0]<3 else 'succeeded',exit_code=1 if count[0]<3 else 0,output='fixture test',changes=[])
        with patch('desk_companion.project_workspace.run',side_effect=command): execute(request,lambda k,d:events.append((k,d)),llm=LLM())
        self.assertEqual((folder/'app.py').read_text(),'value = 3\n')
        self.assertEqual(len([e for e in events if e[0]=='file_change']),2)
        self.assertEqual([e[1]['exit_code'] for e in events if e[0]=='verification'],[1,1,0])
        self.assertIn('实际验证回执',events[-1][1])
        self.assertNotIn('fixture-key',json.dumps(events))

    def test_coder_without_directory_can_generate_code_but_has_no_file_tools(self):
        request = self.request(role=dict(id='coder',name='码可',role='编程伙伴',tools=['工作区读取','文件编辑','终端']))
        request.update(text='帮我写一个冒泡排序', workspace={})
        calls=[]; events=[]
        class LLM:
            def chat(_,messages,tools,on_delta):
                calls.append((messages,tools)); on_delta('```python\ndef bubble_sort(a): return sorted(a)\n```')
                return dict(message=dict(content='代码示例；尚未运行验证。'),usage=dict(prompt_tokens=10,completion_tokens=8))
        with patch('desk_companion.project_workspace.lease') as lease:
            execute(request,lambda k,d:events.append((k,d)),llm=LLM())
        lease.assert_not_called()
        self.assertEqual(len(calls),1); self.assertEqual(calls[0][1],[])
        self.assertTrue(any(k=='token' for k,d in events))
        self.assertIn('无需工作目录',calls[0][0][0]['content'])

    def test_coder_cannot_invent_write_tool_without_directory_or_permission(self):
        from desk_companion import project_workspace as project
        folder=Path(self.temp.name)/'readonly'; folder.mkdir()
        self.action(type='open_folder_project',primary=str(folder))
        cfg=project.save(str(folder),[],False,False)['workspace']
        request=self.request(role=dict(id='coder',name='码可',role='编程伙伴',tools=['工作区读取','文件编辑','终端']))
        seen=[]
        class LLM:
            def chat(_,messages,tools,on_delta):
                seen.extend(t['function']['name'] for t in tools)
                return dict(message=dict(tool_calls=[dict(id='write',function=dict(name='workspace_write',arguments='{"path":"a.py","text":"bad","revision":"new"}'))]),usage={})
        for snapshot in ({},cfg):
            with self.subTest(snapshot=snapshot):
                request['workspace']=snapshot
                with self.assertRaisesRegex(ValueError,'白名单'):
                    execute(request,lambda *_:None,llm=LLM())
                self.assertFalse((folder/'a.py').exists())
        self.assertIn('workspace_read',seen); self.assertNotIn('workspace_write',seen); self.assertNotIn('workspace_run',seen)

    def test_duplicate_messages_and_separate_chat_queue(self):
        action = dict(type="send", conversation="group-research", text="比较材料", target="", mode="task", request_id="same-request-0001")
        self.service.action(action); self.service.action(action)
        self.assertEqual(len(self.state()["runs"]), 1)
        self.service.tick(); route=self.state()['runs'][0]
        self.chat.complete('collab:'+route['id']+':0',json.dumps(dict(mode='task',instructions='比较材料',strategy='direct')))
        self.service.tick();self.service.tick();self.assertEqual(len(self.work.items),1)
        self.action(type="send", text="现在能聊天吗", target="", mode="message")
        self.service.tick(); self.assertEqual(len(self.chat.items), 2)
        self.assertEqual(len(self.work.items), 1)
        with self.assertRaisesRegex(ValueError, "另一项操作"):
            self.service.action({**action, "text": "不同消息"})

    def test_valid_plan_handoff_and_failure_never_uses_empty_result(self):
        self.action(type="send", text="研究并写作", target="", mode="task"); self.service.tick()
        self.work.complete(next(iter(self.work.items)), self.plan())
        self.service.tick(); state = self.state()
        self.assertEqual([r["state"] for r in state["runs"]], ["waiting", "pending", "waiting"])
        self.service.tick(); research = self.state()["runs"][1]
        task = "collab:"+research["id"]+":0"
        self.work.complete(task, "已有来源的真实研究结果", evidence=dict(query="官方来源",text="SOURCE_LEDGER_SENTINEL")); self.service.tick(); self.service.tick()
        writer = self.state()["runs"][2]
        self.assertEqual(writer["state"], "queued")
        self.service.tick(); request = self.work.requests["collab:"+writer["id"]+":0"]
        self.assertIn("已有来源的真实研究结果", json.dumps(request, ensure_ascii=False))
        self.assertIn("SOURCE_LEDGER_SENTINEL", json.dumps(request, ensure_ascii=False))
        self.work.complete("collab:"+writer["id"]+":0", "", state="failed"); self.service.tick()
        self.assertEqual(self.state()["runs"][2]["state"], "failed")
        self.assertNotIn("artifact", self.state()["runs"][2])

    def test_cancel_planner_and_children_does_not_stop_independent_work(self):
        self.action(type="send", text="团队任务", target="", mode="task"); self.service.tick()
        root = self.state()["runs"][0]
        self.work.complete("collab:"+root["id"]+":0", self.plan()); self.service.tick(); self.service.tick()
        self.action(type="send", text="独立查询", target="research", mode="task"); self.service.tick()
        self.action(type="cancel", id=root["id"]); self.service.tick()
        rows = self.state()["runs"]
        self.assertEqual(rows[1]["state"], "cancelled"); self.assertEqual(rows[2]["state"], "cancelled")
        self.assertEqual(rows[-1]["state"], "queued")
        self.assertEqual(rows[3]["state"], "queued")

    def test_failed_dependency_never_dispatches_its_writer(self):
        self.action(type="send", text="团队任务", target="", mode="task"); self.service.tick()
        root = self.state()["runs"][0]
        self.work.complete("collab:"+root["id"]+":0", self.plan()); self.service.tick(); self.service.tick()
        research = self.state()["runs"][1]
        self.work.complete("collab:"+research["id"]+":0", "部分资料", state="failed"); self.service.tick()
        rows = self.state()["runs"]
        self.assertEqual(rows[2]["state"], "cancelled")
        self.assertEqual(len(self.work.items), 2)
        self.assertNotIn("artifact", rows[1])

    def test_late_update_blocks_handoff_and_creates_bounded_revision(self):
        self.action(type="send", text="写一份报告", target="research", mode="task"); self.service.tick()
        run = self.state()["runs"][0]; task = "collab:"+run["id"]+":0"
        self.work.complete(task, "旧稿")
        self.action(type="send", text="重点是部署成本", run=run["id"], mode="task")
        self.service.tick(); self.assertEqual(self.state()["runs"][0]["state"], "pending")
        self.service.tick(); revised = "collab:"+run["id"]+":1"
        self.assertEqual(self.work.requests[revised]["draft"], "旧稿")
        self.work.complete(revised, "按成本修订的成果", applied=1); self.service.tick()
        state = self.state(); self.assertEqual(state["runs"][0]["artifact"], "按成本修订的成果")
        with self.assertRaises(ValueError): self.action(type="send", text="结束后补充", run=run["id"], mode="task")

    def test_private_memory_is_not_in_other_agents_or_group_context(self):
        agent = self.state()["agents"][1]
        self.action(type="agent", agent={**agent, "memory": "RESEARCH_PRIVATE_SENTINEL"})
        self.action(type="send", text="你好", target="writer", mode="message"); self.service.tick()
        request = next(iter(self.chat.requests.values()))
        self.assertNotIn("RESEARCH_PRIVATE_SENTINEL", json.dumps(request))
        self.assertTrue(any(row["sender"] == "group_goal" for row in request["context"]))
        self.assertNotIn("agent_config", self.state()["runs"][0])
        with self.assertRaises(ValueError): self.action(type="agent", agent={**agent, "tools": ["飞书"]})
        with self.assertRaises(ValueError): self.action(type="send", text="@不存在的成员 帮忙", mode="task")

    def test_queue_submission_recovers_without_duplicate_work_after_partial_commit(self):
        self.action(type="send", text="任务", target="research", mode="task"); self.service.tick()
        task = next(iter(self.work.items))
        with transaction(self.service.path) as (state, _):
            state["runs"][0].update(state="pending", task="")
        self.service.tick(); self.assertEqual(len(self.work.items), 1)
        self.work.complete(task, "完成"); self.service.tick(); self.service.tick()
        self.assertEqual(len(self.work.items), 1)

    def test_plans_cannot_escape_members_or_create_cycles(self):
        for value in [dict(tasks=[dict(agent_id="stranger", title="a", instructions="a", depends_on=None)]),
                      dict(tasks=[dict(agent_id="research", title="a", instructions="a", depends_on=0)]),
                      dict(tasks=[dict(agent_id="research", title="a", instructions="a", depends_on=False)])]:
            with self.assertRaises(ValueError): parse_plan(json.dumps(value), {"research"})

    def test_original_goal_survives_a_route_that_narrows_it_to_a_stale_failure(self):
        goal='比较我的项目和外部框架，读取源码并输出报告'
        self.action(type='send',text=goal,mode='auto',target='');self.service.tick()
        root=self.state()['runs'][-1]
        self.chat.complete('collab:'+root['id']+':0',json.dumps(dict(mode='task',instructions='只补外部资料，私有项目尚未授权')))
        self.service.tick();plan=self.state()['runs'][-1]
        self.assertEqual(self.work.items,{})
        self.service.tick();request=list(self.work.requests.values())[-1]
        self.assertEqual(request['text'],goal)
        self.assertNotIn('尚未授权',request['text'])

    def test_child_keeps_assigned_partner_model_and_unassigned_partner_inherits_turn_model(self):
        with patch('desk_companion.model_catalog.require_item',side_effect=lambda ident:{**MODEL,'id':ident,'model':ident}):
            writer=next(a for a in self.state()['agents'] if a['id']=='writer')
            self.action(type='agent',agent={**writer,'model':'custom-writer-model'})
            self.action(type='send',text='研究并写报告',mode='task',target='',model_id='turn-default')
            self.service.tick();root=self.state()['runs'][0]
            self.work.complete('collab:'+root['id']+':0',self.plan());self.service.tick()
            children=self.state()['runs'][1:]
            self.assertEqual(next(r for r in children if r['agent']=='research')['model_id'],'turn-default')
            self.assertEqual(next(r for r in children if r['agent']=='writer')['model_id'],'custom-writer-model')

    def test_parallel_collection_fans_in_all_evidence_before_delivery(self):
        state=self.action(type='agent',agent={**self.state()['agents'][0],'tools':['扩展只读工具']})
        # Include three workers; the coordinator may also participate when explicitly @ selected.
        with transaction(self.service.path) as (s,_):
            s['agents'].append(dict(id='repo',name='源码伙伴',role='阅读项目',model='',tools=['扩展只读工具'],memory='',mark='C',tone='sage'))
            s['conversations'][0]['members'].append('repo')
        self.action(type='send',text='完整对比报告',mode='task',target='');self.service.tick()
        root=self.state()['runs'][0]
        rows=[dict(agent_id='repo',title='项目源码',instructions='读项目',stage='collect',required_sources=['repository'],depends_on=None),
              dict(agent_id='research',title='公开资料',instructions='读外部资料',stage='collect',required_sources=['web'],source_topics=['框架甲','框架乙','平台丙'],depends_on=None),
              dict(agent_id='writer',title='最终对比',instructions='综合全部资料',stage='collect',depends_on=None)]
        self.work.complete('collab:'+root['id']+':0',json.dumps(dict(tasks=rows)));self.service.tick();self.service.tick()
        repo,research,writer=self.state()['runs'][1:4]
        self.assertEqual(writer['state'],'waiting');self.assertEqual(writer['stage'],'deliver')
        self.assertEqual(set(writer['dependencies']),{repo['id'],research['id']})
        self.assertEqual(self.state()['runs'][0]['chain']['state'],'running')
        research_req=self.work.requests['collab:'+research['id']+':0']
        self.assertEqual((research_req['search_budget'],research_req['fetch_budget']),(3,6))
        self.work.complete('collab:'+repo['id']+':0','源码结果',evidence=dict(kind='github',sources=[dict(kind='github',url='https://github.com/u/r/blob/main/core.py',level='body',text='SOURCE_CODE')]))
        self.service.tick();self.assertEqual(self.state()['runs'][-1]['state'],'waiting')
        self.work.complete('collab:'+research['id']+':0','公开资料结果',evidence=dict(kind='page',url='https://example.org/doc',level='body',text='PUBLIC_BODY'))
        self.service.tick();self.service.tick()
        req=self.work.requests['collab:'+writer['id']+':0']
        self.assertIn('SOURCE_CODE',json.dumps(req));self.assertIn('PUBLIC_BODY',json.dumps(req))
        self.assertEqual(set(req['required_sources']),{'repository','web'})
        self.assertEqual(req['objective'],'完整对比报告')
        self.work.complete('collab:'+writer['id']+':0','最终报告',state='failed');self.service.tick()
        self.assertEqual(self.state()['runs'][0]['chain']['state'],'failed')

    def test_citation_repair_has_a_reserved_call_and_no_extra_fetch_is_executed(self):
        request=self.request();request['budget']=4;events=[];calls=[];queries=[]
        class LLM:
            def chat(_,messages,tools,on_delta):
                calls.append(copy.deepcopy(tools))
                if len(calls)<3:return dict(message=dict(role='assistant',tool_calls=[dict(id=str(len(calls)),function=dict(name='web_search',arguments='{"query":"official"}'))]),usage={})
                return dict(message=dict(content='没有链接的草稿' if len(calls)==3 else '实际来源 [官方](https://example.org/doc)'),usage={})
        execute(request,lambda k,d:events.append((k,d)),llm=LLM(),search=lambda q:queries.append(q) or '实际摘要 https://example.org/doc')
        self.assertEqual(len(calls),4);self.assertEqual(len(queries),2)
        self.assertEqual(calls[-2:], [[],[]]);self.assertEqual([v['ok'] for k,v in events if k=='citation_check'],[False,True])

    def test_final_delivery_requires_real_body_sources_and_explicit_complete_result(self):
        from desk_companion.collaboration_worker import PublicFailure
        request=self.request(role=dict(id='writer',name='墨墨',role='综合报告',tools=[]))
        request.update(stage='deliver',required_sources=['repository','web'])
        sources=[dict(kind='page',url='https://example.org/doc',level='body',text='public doc'),
                 dict(kind='github',sources=[dict(kind='github',url='https://github.com/u/r/blob/main/core.py',level='body',text='core source')])]
        class LLM:
            complete=True
            def chat(_,messages,tools,on_delta):
                return dict(message=dict(tool_calls=[dict(id='done',function=dict(name='submit_deliverable',arguments=json.dumps(dict(report='比较依据 [公开](https://example.org/doc) [源码](https://github.com/u/r/blob/main/core.py)',complete=_.complete,missing=[] if _.complete else ['仅框架，核心对比未完成'],limitations=['未要求的额外版本信息未核实']))))]),usage={})
        llm=LLM();events=[];request['source_evidence']=sources
        execute(request,lambda k,d:events.append((k,d)),llm=llm)
        self.assertEqual(events[-1][0],'result');self.assertTrue(next(v for k,v in events if k=='delivery_check')['ok'])
        self.assertEqual(next(v for k,v in events if k=='delivery_check')['limitations'],['未要求的额外版本信息未核实'])
        llm.complete=False
        with self.assertRaisesRegex(PublicFailure,'未完成'):execute(request,lambda *_:None,llm=llm)
        llm.complete=True;request['source_evidence']=sources[:1]
        with self.assertRaises(PublicFailure):execute(request,lambda *_:None,llm=llm)

    def test_delivery_body_streams_before_tool_completion_and_replaces_preamble(self):
        request=self.request(role=dict(id='writer',name='墨墨',role='综合报告',tools=[]))
        request.update(stage='deliver');events=[];report='# 报告\n\n这是逐段生成的正文。'
        class LLM:
            def chat(_,messages,tools,on_delta):
                _.on_reasoning('PRIVATE_REASONING');_.on_reasoning('PRIVATE_REASONING')
                on_delta('我来整理…')
                raw=json.dumps(dict(report=report,complete=True,missing=[]),ensure_ascii=False)
                for end in range(1,len(raw)+1):_.on_tool_delta(0,'submit_deliverable',raw[:end])
                self.assertFalse(any(k=='result' for k,v in events))
                reset=max(i for i,(k,v) in enumerate(events) if k=='answer_reset')
                self.assertEqual(''.join(v for k,v in events[reset+1:] if k=='token'),report)
                return dict(message=dict(tool_calls=[dict(id='delivery',function=dict(name='submit_deliverable',arguments=raw))]),usage={})
        execute(request,lambda k,d:events.append((k,d)),llm=LLM())
        self.assertEqual(events[-1],('result',report))
        self.assertEqual(sum(k=='status' and v=='正在思考与组织回答。' for k,v in events),1)
        self.assertNotIn('PRIVATE_REASONING',str(events))

    def test_repository_tool_events_do_not_consume_search_quota(self):
        self.action(type='send',text='读取项目',target='research',mode='task');self.service.tick()
        run=self.state()['runs'][-1];task='collab:'+run['id']+':0'
        self.work.items[task]['events']=[dict(seq=1,kind='tool_start',data=dict(tool='github_repository'))]
        self.service.tick();self.assertEqual(self.state()['runs'][-1]['search_calls'],0)

    def test_a_denied_url_does_not_spend_the_actual_network_request_budget(self):
        request=self.request();request['fetch_budget']=1;calls=[];events=[];fetched=[]
        class LLM:
            def chat(_,messages,tools,on_delta):
                calls.append(copy.deepcopy(messages));step=len(calls)
                if step==4:return dict(message=dict(content='已读 [正文](https://example.org/real)'),usage={})
                name='web_search' if step==2 else 'web_fetch'
                args=dict(query='official') if step==2 else dict(url='https://example.org/guessed' if step==1 else 'https://example.org/real')
                return dict(message=dict(role='assistant',tool_calls=[dict(id=str(step),function=dict(name=name,arguments=json.dumps(args)))]),usage={})
        def fetch(url):
            fetched.append(url);return dict(kind='page',level='body',url=url,text='Actual body')
        execute(request,lambda k,d:events.append((k,d)),llm=LLM(),search=lambda q:'来源 https://example.org/real',fetch=fetch)
        self.assertEqual(fetched,['https://example.org/real'])
        self.assertEqual(sum(k=='tool_start' and v['tool']=='web_fetch' for k,v in events),1)
        self.assertTrue(any(k=='tool_end' and '未占用' in v.get('error','') for k,v in events))

    def test_pseudo_tool_markup_is_repaired_without_executing_it_or_leaking_reasoning(self):
        request=self.request();request['source_evidence']=[dict(kind='page',level='body',url='https://example.org/real',text='Body')]
        calls=[];events=[]
        class LLM:
            def chat(_,messages,tools,on_delta):
                calls.append(copy.deepcopy(messages))
                return dict(message=dict(content='<tool_call><function=web_fetch>https://example.org/real</function></tool_call>' if len(calls)==1 else '完整报告 [来源](https://example.org/real)',reasoning_content='PRIVATE_REASONING'),usage={})
        with patch('desk_companion.web_sources.fetch_page') as fetch:
            execute(request,lambda k,d:events.append((k,d)),llm=LLM());fetch.assert_not_called()
        self.assertEqual(len(calls),2);self.assertEqual(events[-1][0],'result')
        self.assertTrue(any(m.get('reasoning_content')=='PRIVATE_REASONING' for m in calls[-1]))
        self.assertNotIn('PRIVATE_REASONING',json.dumps(events));self.assertFalse(any(k=='tool_start' for k,v in events))

    def test_model_uses_evidence_ids_and_unknown_ids_are_not_accepted(self):
        from desk_companion.collaboration_worker import PublicFailure
        request=self.request();request['source_evidence']=[dict(kind='page',level='body',url='https://example.org/actual',text='Body')]
        from desk_companion.web_sources import source_catalog
        ident=source_catalog(request['source_evidence'])[0]['id']
        events=[]
        class LLM:
            text=f'比较结论 [{ident}]'
            def chat(_,messages,tools,on_delta):return dict(message=dict(content=_.text),usage={})
        llm=LLM();execute(request,lambda k,d:events.append((k,d)),llm=llm)
        self.assertIn('[1](<https://example.org/actual>)',events[-1][1])
        llm.text=f'真实 [{ident}] 与伪造 [S9]'
        with self.assertRaises(PublicFailure):execute(request,lambda *_:None,llm=llm)

    def request(self, role=None):
        self.action(type="send", text="根据材料写作", target="research", mode="task"); self.service.tick()
        request = copy.deepcopy(next(iter(self.work.requests.values())))
        request["limits"] = dict(call_timeout=10, task_timeout=30)
        if role is not None: request["role"] = role
        return request

    def test_real_worker_search_then_response_and_usage(self):
        request = self.request(); events = []; inputs = []; queries = []
        class LLM:
            def chat(_, messages, tools, on_delta):
                inputs.append(copy.deepcopy(messages))
                if len(inputs) == 1:
                    return dict(message=dict(role="assistant", content="", tool_calls=[dict(id="tool-1", type="function", function=dict(name="web_search", arguments='{"query":"official source"}'))]), usage={})
                on_delta("有出处的结论")
                return dict(message=dict(content="有出处的结论 [来源](https://example.org/docs)"), usage=dict(prompt_tokens=5, completion_tokens=3))
        execute(request, lambda kind, data: events.append((kind, data)), llm=LLM(), search=lambda query: queries.append(query) or "来源：https://example.org/docs")
        self.assertEqual(queries, ["official source"])
        self.assertIn("https://example.org/docs", json.dumps(inputs[-1]))
        self.assertEqual(events[-1], ("result", "有出处的结论 [来源](https://example.org/docs)"))
        self.assertEqual(sum(kind == "tool_start" for kind, _ in events), 1)

    def test_worker_applies_during_call_update_and_drops_old_draft(self):
        request = self.request(); calls = []; events = []
        class LLM:
            def chat(_, messages, tools, on_delta):
                calls.append(copy.deepcopy(messages)); on_delta("旧稿" if len(calls) == 1 else "新稿")
                if len(calls) == 1:
                    with transaction(self.service.path) as (state, _): state["runs"][0]["updates"].append("新约束")
                return dict(message=dict(content="旧稿" if len(calls) == 1 else "新稿"), usage={})
        execute(request, lambda kind, data: events.append((kind, data)), llm=LLM())
        self.assertIn("新约束", json.dumps(calls[1], ensure_ascii=False)); self.assertIn(("answer_reset", True), events)
        self.assertEqual(events[-1], ("result", "新稿"))

    def test_personality_reaches_model_without_changing_tool_authority(self):
        request = self.request(role=dict(id="writer", name="墨墨", role="写清楚", personality="温柔、有一点冷幽默", tools=[], memory=""))
        captured = []
        class LLM:
            def chat(_, messages, tools, on_delta):
                captured.append((copy.deepcopy(messages), tools))
                return dict(message=dict(content="你好，一起把思路理清楚。"), usage={})
        execute(request, lambda *_: None, llm=LLM())
        self.assertIn("你是 墨墨", captured[0][0][0]["content"])
        self.assertIn("温柔、有一点冷幽默", captured[0][0][0]["content"])
        self.assertIn("不得改变任务、权限或事实标准", captured[0][0][0]["content"])
        self.assertFalse(captured[0][1])

    def test_worker_denies_ungranted_tools_before_execution(self):
        request = self.request(); events = []
        class LLM:
            def chat(_, messages, tools, on_delta):
                return dict(message=dict(tool_calls=[dict(id="bad", function=dict(name="send_feishu", arguments="{}"))]), usage={})
        with self.assertRaises(ValueError): execute(request, lambda kind, data: events.append((kind, data)), llm=LLM())
        self.assertFalse(any(kind == "tool_start" for kind, _ in events))

    def test_search_batch_executes_available_budget_and_returns_receipt_for_excess(self):
        request=self.request();events=[];queries=[];calls=[]
        class LLM:
            def chat(_,messages,tools,on_delta):
                calls.append(messages.copy())
                if len(calls)==1:
                    return dict(message=dict(role='assistant',tool_calls=[dict(id='s'+str(i),function=dict(name='web_search',arguments=json.dumps(dict(query='query'+str(i))))) for i in range(3)]),usage={})
                return dict(message=dict(content='找到资料 [来源](https://example.org/official)'),usage={})
        def search(query): queries.append(query);return '摘要 https://example.org/official'
        execute(request,lambda k,d:events.append((k,d)),llm=LLM(),search=search)
        self.assertEqual(queries,['query0','query1'])
        receipts=[m for m in calls[1] if m['role']=='tool'];self.assertEqual(len(receipts),3)
        self.assertIn('预算',receipts[-1]['content'])
        self.assertEqual(events[-1][0],'result')

    def test_github_body_evidence_survives_a_blocked_anonymous_fetch(self):
        request=self.request(role=dict(id='coder',name='码可',role='分析仓库',tools=['扩展只读工具','网页读取']))
        request['workspace']={};calls=[];events=[]
        source=dict(kind='github',level='body',url='https://github.com/owner/Atlas/blob/main/README.md',title='Atlas',text='actual source',truncated=False)
        class LLM:
            def chat(_,messages,tools,on_delta):
                calls.append(messages.copy())
                if len(calls)==1:return dict(message=dict(role='assistant',tool_calls=[dict(id='g',function=dict(name='github_repository',arguments='{"operation":"readme","repo":"Atlas"}'))]),usage={})
                return dict(message=dict(content='源码分析 [README](https://github.com/owner/Atlas/blob/main/README.md)'),usage={})
        with patch('desk_companion.github_repository.SPEC',dict(name='github_repository',description='read',parameters={},func=lambda _:dict(ok=True,source=source),isReadOnly=True)):
            execute(request,lambda k,d:events.append((k,d)),llm=LLM())
        self.assertTrue(any(k=='evidence' and d.get('kind')=='github' for k,d in events))
        self.assertEqual(events[-1][0],'result')

    def test_failed_search_can_rephrase_within_budget_and_keeps_actual_evidence(self):
        request = self.request(); events = []; calls = []; searches = []
        class LLM:
            def chat(_, messages, tools, on_delta):
                calls.append(copy.deepcopy(messages))
                if len(calls) < 3:
                    return dict(message=dict(role="assistant",tool_calls=[dict(id="tool-"+str(len(calls)),type="function",function=dict(name="web_search",arguments=json.dumps(dict(query="query-"+str(len(calls))))))]),usage={})
                return dict(message=dict(content="有证据的结果 [来源](https://example.org/verified)"),usage={})
        def search(query):
            searches.append(query)
            if len(searches)==1: raise RuntimeError("没有结果 fixture-key")
            return "来源：https://example.org/verified"
        execute(request,lambda kind,data:events.append((kind,data)),llm=LLM(),search=search)
        self.assertEqual(len(searches),2);self.assertNotIn("fixture-key",json.dumps(events))
        self.assertTrue(any(kind=="evidence" and "verified" in data["text"] for kind,data in events))
        self.assertEqual(events[-1],("result","有证据的结果 [来源](https://example.org/verified)"))

    def test_no_successful_search_never_delivers_an_unverified_report(self):
        from desk_companion.collaboration_worker import PublicFailure
        request=self.request();events=[];calls=[]
        class LLM:
            def chat(_,messages,tools,on_delta):
                calls.append(True)
                if len(calls)==1: return dict(message=dict(role="assistant",tool_calls=[dict(id="tool-1",type="function",function=dict(name="web_search",arguments='{"query":"official"}'))]),usage={})
                return dict(message=dict(content="无依据的报告"),usage={})
        def search(_): raise RuntimeError("没有结果 fixture-key")
        with self.assertRaises(PublicFailure) as error: execute(request,lambda kind,data:events.append((kind,data)),llm=LLM(),search=search)
        self.assertNotIn("fixture-key",str(error.exception));self.assertFalse(any(kind=="result" for kind,_ in events))

    def test_search_fetch_and_citation_provenance_use_actual_body(self):
        request=self.request();events=[];calls=[]
        class LLM:
            def chat(_,messages,tools,on_delta):
                calls.append(copy.deepcopy(messages))
                name='web_search' if len(calls)==1 else 'web_fetch'
                if len(calls)<3:
                    args={'query':'official'} if name=='web_search' else {'url':'https://example.org/doc'}
                    return dict(message=dict(role='assistant',tool_calls=[dict(id=str(len(calls)),function=dict(name=name,arguments=json.dumps(args)))]),usage={})
                return dict(message=dict(content='结论 [正文](https://example.org/doc)'),usage={})
        page=dict(kind='page',level='body',url='https://example.org/doc',text='BODY_SENTINEL',truncated=False)
        execute(request,lambda k,d:events.append((k,d)),llm=LLM(),search=lambda q:dict(results=[dict(title='Official',link=page['url'],snippet='Summary')]),fetch=lambda u:page)
        self.assertIn('BODY_SENTINEL',json.dumps(calls[-1]));audit=next(d for k,d in events if k=='citation_check')
        self.assertTrue(audit['ok']);self.assertEqual(audit['pages_read'],1)
        self.assertEqual(audit['matched'][0]['level'],'body')

    def test_writer_receives_body_evidence_and_repairs_unretrieved_citation(self):
        request=self.request(role=dict(id='writer',name='写作者',role='写报告',tools=[]))
        request['source_evidence']=[dict(kind='page',level='body',url='https://example.org/real',text='BODY_FOR_WRITER',truncated=False)]
        calls=[];events=[]
        class LLM:
            def chat(_,messages,tools,on_delta):
                calls.append(copy.deepcopy(messages))
                return dict(message=dict(content='[source](https://example.org/'+('fake' if len(calls)==1 else 'real')+')'),usage={})
        execute(request,lambda k,d:events.append((k,d)),llm=LLM())
        self.assertEqual(len(calls),2);self.assertIn('BODY_FOR_WRITER',json.dumps(calls[0]))
        self.assertIn(('answer_reset',True),events)
        self.assertEqual([d['ok'] for k,d in events if k=='citation_check'],[False,True])

    def test_fetch_permission_and_known_url_are_enforced_and_no_fake_evidence(self):
        from desk_companion.collaboration_worker import PublicFailure
        class LLM:
            def chat(_,messages,tools,on_delta):
                return dict(message=dict(role='assistant',tool_calls=[dict(id='fetch',function=dict(name='web_fetch',arguments='{"url":"https://example.org/not-discovered"}'))]),usage={})
        events=[];request=self.request(role=dict(name='writer',role='写作',tools=[]))
        with self.assertRaises(ValueError):execute(request,lambda k,d:events.append((k,d)),llm=LLM())
        self.assertFalse(any(k=='tool_start' for k,d in events))
        events=[];request=self.request()
        with patch('desk_companion.web_sources.fetch_page') as fetch:
            with self.assertRaisesRegex(PublicFailure,'预算'):execute(request,lambda k,d:events.append((k,d)),llm=LLM())
            fetch.assert_not_called()
        self.assertFalse(any(k=='evidence' for k,d in events))

    def test_repeated_invented_citations_fail_without_handoff(self):
        from desk_companion.collaboration_worker import PublicFailure
        request=self.request();request['source_evidence']=[dict(kind='page',level='body',url='https://example.org/real',text='Real')]
        events=[];calls=[]
        class LLM:
            def chat(_,messages,tools,on_delta):
                calls.append(True)
                return dict(message=dict(content='[fake](https://example.org/fabricated)'),usage={})
        with self.assertRaises(PublicFailure):execute(request,lambda k,d:events.append((k,d)),llm=LLM())
        self.assertEqual(len(calls),2);self.assertFalse(any(k=='result' for k,d in events))

    def test_own_source_evidence_is_carried_into_bounded_revision(self):
        self.action(type='send',text='研究任务',target='research',mode='task');self.service.tick()
        run=self.state()['runs'][0];task='collab:'+run['id']+':0'
        page=dict(kind='page',url='https://example.org/body',level='body',text='PERSISTED_BODY')
        self.work.complete(task,'旧稿',evidence=page)
        self.work.items[task]['events'].extend([dict(seq=5,kind='tool_start',data={'tool':'web_search'}),dict(seq=6,kind='tool_start',data={'tool':'web_fetch'})])
        self.action(type='send',text='修订',run=run['id'],mode='task');self.service.tick();self.service.tick()
        req=self.work.requests['collab:'+run['id']+':1']
        self.assertEqual(req['search_budget'],1);self.assertEqual(req['fetch_budget'],3)
        self.assertIn('PERSISTED_BODY',json.dumps(req))


class PreviewAccessTests(unittest.TestCase):
    def test_first_collaboration_connection_does_not_wait_for_desktop_chat_lock(self):
        from desk_companion.local_api.host import HeadlessApp
        host=HeadlessApp.__new__(HeadlessApp)
        host.turn_lock=threading.RLock();host._collaboration_lock=threading.Lock();host._collaboration=None
        ready=threading.Event();sentinel=object()
        def connect():
            if host.collaboration is sentinel:ready.set()
        with patch("desk_companion.collaboration.Collaboration",return_value=sentinel):
            with host.turn_lock:
                thread=threading.Thread(target=connect);thread.start()
                self.assertTrue(ready.wait(1))
            thread.join(1)

    def test_capability_expires_and_requires_an_exact_local_origin(self):
        from desk_companion.collaboration_access import preview_allowed, access_path
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"DESK_COMPANION_DATA_DIR": temp}):
            path = access_path(); path.parent.mkdir(parents=True)
            path.write_text(json.dumps(dict(token="x"*48, expires=200)), "utf-8")
            with patch("time.time", return_value=100):
                self.assertTrue(preview_allowed("x"*48, "http://127.0.0.1:5180"))
                self.assertFalse(preview_allowed("x"*48, "https://example.org"))
                self.assertFalse(preview_allowed("y"*48, "http://127.0.0.1:5180"))
            with patch("time.time", return_value=201): self.assertFalse(preview_allowed("x"*48, "http://127.0.0.1:5180"))


class PreviewRpcTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_capability_cannot_access_general_rpc_or_chat(self):
        from desk_companion.local_api import server
        class Socket:
            def __init__(self):
                from types import SimpleNamespace
                self.request = SimpleNamespace(path="/collaboration?token="+"x"*48, headers={"Origin":"http://127.0.0.1:5180"})
                self.messages = iter([dict(type="rpc",method="terminal_start",args={}),dict(type='rpc',method='load_model_network'),dict(type='rpc',method='save_model_network',args={'payload':{'mode':'direct'}}), dict(type="chat",text="bad"), dict(type="rpc",method="load_collaboration")]); self.replies=[]
            def __aiter__(self): return self
            async def __anext__(self):
                try: return json.dumps(next(self.messages))
                except StopIteration: raise StopAsyncIteration
            async def send(self, raw): self.replies.append(json.loads(raw))
            async def close(self, **kwargs): raise AssertionError("valid scoped connection should not be closed")
        socket = Socket()
        with patch("desk_companion.collaboration_access.preview_allowed", return_value=True), patch.object(server,"_dispatch",return_value=dict(ok=True,result={})) as dispatch:
            await server._handler(socket)
        self.assertEqual(dispatch.call_count,1);self.assertEqual(dispatch.call_args.args[0],"load_collaboration")
        self.assertEqual([reply["type"] for reply in socket.replies],["error","error","error","error","rpc_result"])


if __name__ == "__main__": unittest.main()
