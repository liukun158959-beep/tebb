"""Product, Skill and configured MCP adapters for the tebb execution loop."""
from .worker_rpc import call


def specs(request):
    run_id=request['collab_run']
    def spec(name,description,properties,required,implementation,readonly=True):
        return dict(type='function',function=dict(name=name,description=description,parameters=dict(type='object',properties=properties,required=required)),implementation=implementation,read_only=readonly)
    def product(name,args):
        return call('tebb_operation',dict(run_id=run_id,tool=name,args=args))
    string=dict(type='string')
    out=[
        spec('calendar_query','查询指定北京时间日期或月份的本地及飞书日程。month=true读取该月；query可按标题过滤。先查再按准确身份修改，重名或意图不明先询问用户。',dict(day=string,month=dict(type='boolean'),query=string,request_id=string),['day'],lambda args:product('calendar_query',args)),
        spec('calendar_change','修改日程。action.type=create/update/sync/retime/delete/resolve_delete；新建默认本地，仅用户明确飞书才随后sync。参数沿用日程服务：summary/start/end含时区/item_id，更新传local_id/version；改期传calendar_id/event_id/day/original_start/original_end/start/end，改标题同时传summary和original_summary（已同步必须用此入口）；删除传calendar_id/event_id与原时间（本地另传version）。操作编号由本轮call_id自动登记。失败先核对编号，不自动重做。',dict(action=dict(type='object')),['action'],lambda args:product('calendar_change',args),False),
        spec('automation_list','读取本地自动化规则、状态及上次执行。',{},[],lambda args:product('automation_list',args)),
        spec('automation_save','保存自动化。payload含name/action=tebb_task/goal/project_id/agent_id默认tebb/enabled/cadence=once|daily|weekly/hour/minute/weekdays(周一0至周日6)/date(一次时YYYY-MM-DD)/doc空；修改已有规则须带id。缺目标或时间先问，不得擅自扩大目标。后台运行时触发。',dict(payload=dict(type='object')),['payload'],lambda args:product('automation_save',args),False),
        spec('automation_manage','明确删除或立即执行现有自动化：action=delete|run|check,job_id。先读取精确规则身份。',dict(action=dict(type='string',enum=['delete','run','check']),job_id=string),['action','job_id'],lambda args:product('automation_manage',args),False),
    ]
    from .assistant import LIST_SKILLS_SPEC,READ_SKILL_SPEC,REMEMBER_SPEC,FORGET_SPEC,STATUS_SPEC,ROADMAP_SPEC,RECENT_SPEC,REPOSITORY_SPEC,ERROR_LOG_SPEC,AGENDA_SPEC,TASKS_SPEC
    from .video_tools import specs as video_specs
    from .facts import bind_turn_user
    def bound(func):
        def invoke(args):
            with bind_turn_user(request.get('objective',request['text'])):return func(args)
        return invoke
    for item in (*video_specs(lambda:'collaboration-'+request.get('session_id',run_id),lambda status:None),LIST_SKILLS_SPEC,READ_SKILL_SPEC,REMEMBER_SPEC,FORGET_SPEC,STATUS_SPEC,ROADMAP_SPEC,RECENT_SPEC,REPOSITORY_SPEC,ERROR_LOG_SPEC,AGENDA_SPEC,TASKS_SPEC):
        out.append(dict(type='function',function={k:item[k] for k in ('name','description','parameters')},implementation=bound(item['func']),read_only=item.get('isReadOnly',True)))
    from . import mcp_client as mcp
    def catalog(args):
        servers=mcp.load_mcp_config()
        server=args.get('server')
        if not server:return dict(servers=list(servers))
        if server not in servers:raise ValueError('MCP服务未配置。')
        return dict(server=server,tools=mcp.list_tools(server,servers[server]))
    out.append(spec('mcp_catalog','发现已配置MCP服务器；提供server则读取该服务工具及参数。配置/凭据不返回。',dict(server=string),[],catalog))
    out.append(spec('mcp_call','按发现的服务/工具及其真实参数调用MCP，不猜工具名；失败不自动重试外部写入。',dict(server=string,tool=string,arguments=dict(type='object')),['server','tool','arguments'],lambda args:mcp.call_mcp_tool(args['server'],args['tool'],args['arguments']),False))
    return out
