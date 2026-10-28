"""Pure business-item rules and project-detachment contract; no storage or queues."""
import copy
from datetime import date
import time
import uuid

STATES = ('todo', 'doing', 'review', 'done')
PRIORITIES = ('high', 'normal', 'low')
EXCLUDED_RUNS = {'route', 'chat', 'compress'}


def detach_project(state, project):
    """Called inside the project owner's transaction; no files or runs are changed."""
    for item in state.get('work_items', []):
        if item.get('project_id') == project['id']:
            item.update(project_id='', removed_project=project['name'],
                        version=item['version'] + 1, updated=time.time())


class BoardRuleError(ValueError):
    def __init__(self, message, *, conflict=False):
        super().__init__(message)
        self.conflict = conflict


def _catalog(state):
    projects = [dict(id=p['id'], name=p['name']) for p in state['projects']]
    names = {p['id']: p['name'] for p in projects}
    conversations = {c['id']: c for c in state['conversations']}
    agents = {a['id']: a['name'] for a in state['agents']}
    runs = []
    for run in state['runs']:
        if run.get('kind') in EXCLUDED_RUNS and run.get('state')!='needs_input' and not run.get('source'):
            continue
        conversation = conversations.get(run['conversation'], {})
        project = run.get('workspace', {}).get('project_id', conversation.get('project_id', ''))
        from .execution_flow import chain_view
        chain=chain_view(run,state['runs'])
        runs.append(dict(id=run['id'], title=run['title'], conversation=run['conversation'],
            conversation_title=conversation.get('title', '原对话已移除'),
            project_id=project, project_name=names.get(project, '原项目已移除' if project else '未归属项目'),
            agent=agents.get(run['agent'], '原伙伴已移除'), state=chain['state'],
            delivery_state=run.get('delivery_state', ''), kind=run.get('kind', 'task'),
            status=run.get('clarification') or run.get('status', ''), error=chain['error'],
            answer=run.get('answer', ''), created=run.get('created', 0)))
    return projects, runs


def public_page(state):
    from .schedule_domain import item_schedule_view
    projects, runs = _catalog(state)
    names = {p['id']: p['name'] for p in projects}
    items = copy.deepcopy(state.get('work_items', []))
    for item in items:
        item.setdefault('agent_id','tebb')
        item['schedules'] = item_schedule_view(state, item['id'])
        item['project_name'] = names.get(item['project_id'], '')
        # Handle data restored from a snapshot without its original project too.
        if item['project_id'] and item['project_id'] not in names:
            item['removed_project'] = item.get('project_name_snapshot', '原项目')
            item['project_id'] = ''
    items.sort(key=lambda i: (PRIORITIES.index(i['priority']), i['due'] or '9999', i['created'], i['id']))
    return dict(ok=True, revision=state.get('revision', 0), items=items, projects=projects, runs=runs, agents=[{k:a.get(k,'') for k in ('id','name','role')} for a in state['agents']])


def _fields(payload, state):
    if not isinstance(payload, dict) or set(payload) - {'title', 'description', 'project_id', 'priority', 'due', 'agent_id'}:
        raise BoardRuleError('事项字段无效。')
    title, description = payload.get('title'), payload.get('description', '')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
        raise BoardRuleError('事项标题须为 1 至 120 字。')
    if not isinstance(description, str) or len(description) > 8000:
        raise BoardRuleError('事项说明最多 8000 字。')
    project = payload.get('project_id', '')
    if not isinstance(project, str) or (project and not any(p['id'] == project for p in state['projects'])):
        raise BoardRuleError('项目已移除或不存在，请重新选择。')
    priority, due = payload.get('priority', 'normal'), payload.get('due', '')
    if priority not in PRIORITIES or not isinstance(due, str):
        raise BoardRuleError('优先级或截止日期无效。')
    if due:
        try:
            if date.fromisoformat(due).isoformat() != due:
                raise ValueError()
        except ValueError:
            raise BoardRuleError('截止日期须为 YYYY-MM-DD。') from None
    agent_id=payload.get('agent_id','tebb')
    if not isinstance(agent_id,str) or not any(a['id']==agent_id for a in state['agents']):
        raise BoardRuleError('负责人已移除，请重新选择。')
    name = next((p['name'] for p in state['projects'] if p['id'] == project), '')
    return dict(title=title.strip(), description=description, project_id=project,
                project_name_snapshot=name, removed_project='', priority=priority, due=due, agent_id=agent_id)


def require_item(state, item_id):
    item = next((i for i in state.get('work_items', []) if i['id'] == item_id), None)
    if item is None:
        raise BoardRuleError('事项不存在，请重新读取。')
    return item


def check_version(item, version):
    if type(version) is not int or version != item['version']:
        raise BoardRuleError('事项已被其他操作更新，请重新读取后再操作。', conflict=True)


def touch_item(item):
    item['version'] += 1
    item['updated'] = time.time()


def apply_change(state, action):
    kind = action.get('type')
    items = state.setdefault('work_items', [])
    if kind == 'create':
        if len(items) >= 20000:
            raise BoardRuleError('事项数量已达到 20000。')
        fields = _fields(action.get('fields'), state)
        item = dict(id=uuid.uuid4().hex, **fields, state='todo', archived=False,
                    version=1, created=time.time(), updated=time.time(), links=[])
        items.append(item)
        return item
    item = require_item(state, action.get('id'))
    check_version(item, action.get('version'))
    if kind == 'save':
        fields=action.get('fields')
        if isinstance(fields,dict):fields=dict({'agent_id':item.get('agent_id','tebb')},**fields)
        item.update(_fields(fields, state))
    elif kind == 'move':
        if action.get('state') not in STATES:
            raise BoardRuleError('事项状态无效。')
        if item['archived']:
            raise BoardRuleError('请先恢复已归档事项，再修改状态。')
        item['state'] = action['state']
    elif kind == 'archive':
        if type(action.get('archived')) is not bool:
            raise BoardRuleError('归档状态必须是开关。')
        item['archived'] = action['archived']
    elif kind == 'link':
        _, runs = _catalog(state)
        run = next((r for r in runs if r['id'] == action.get('run_id')), None)
        if run is None:
            raise BoardRuleError('执行记录不存在或不属于本批支持的伙伴工作记录，请重新读取。')
        if not any(link['id'] == run['id'] for link in item['links']):
            if len(item['links']) >= 50:
                raise BoardRuleError('每件事项最多关联 50 条执行记录。')
            item['links'].append({key: run[key] for key in ('id', 'title', 'conversation', 'project_name')})
    elif kind == 'unlink':
        item['links'] = [link for link in item['links'] if link['id'] != action.get('run_id')]
    else:
        raise BoardRuleError('不支持的事项操作。')
    touch_item(item)
    return item
