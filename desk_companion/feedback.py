"""Receipt-backed checks and bounded repair, independent of model transport."""
import re

from .json_document import json_answer


def check(ident,dimension,status,message,*,basis='deterministic',required=True,**evidence):
    if status not in {'pass','fail','unknown'}:raise ValueError('invalid feedback status')
    return dict(id=ident,dimension=dimension,status=status,message=message,basis=basis,required=required,**evidence)


def output_contract(goal,declared=None):
    if declared is not None:
        if not isinstance(declared,dict)or set(declared)-{'format','schema'}or declared.get('format')!='json':
            raise ValueError('unsupported output contract')
        if 'schema'in declared:
            from jsonschema import Draft202012Validator
            schema=declared['schema']
            def local_refs(value):
                if isinstance(value,dict):
                    for key,item in value.items():
                        if key in {'$ref','$dynamicRef'}and (not isinstance(item,str)or not item.startswith('#')):
                            raise ValueError('external schema references are not allowed')
                        local_refs(item)
                elif isinstance(value,list):
                    for item in value:local_refs(item)
            local_refs(schema);Draft202012Validator.check_schema(schema)
        return declared.copy()
    # Conservative standalone instruction only; quoted examples/explanations do
    # not become requirements. More complex goals need an explicit contract.
    instructions=re.sub(r'```[\s\S]*?```|`[^`\n]*`|“[^”]*”|‘[^’]*’|"[^"\n]*"', ' ',goal)
    matches=list(re.finditer(r'(?:^|[。；;，,：:】\n])\s*(?:最终)?(?:只|仅)(?:能)?(?:输出|返回)(?:相同|同样|一个)?\s*(?:合法|有效|标准)?\s*(JSON|Markdown)\s*(对象)?(?=$|[。；;：:，,\n])',instructions,re.I))
    formats=[(m.start(),m[1],bool(m[2]))for m in matches]
    english=r'(?:^|[.;\n])\s*(?:(?:return|output)\s+(?:only|just)|only\s+(?:return|output))\s+(JSON|Markdown)(\s+object)?(?=$|[.,:;\n])'
    formats.extend((m.start(),m[1],bool(m[2]))for m in re.finditer(english,instructions,re.I))
    if formats:
        _,kind,object_only=max(formats,key=lambda value:value[0])
        if kind.lower()=='markdown':return None
        return dict(format='json',**({'schema':{'type':'object'}}if object_only else {}))
    return None


def check_output(text,goal,declared=None):
    contract=output_contract(goal,declared)
    if contract is None:return [check('output_format','format','unknown','没有声明可确定验证的输出格式。',required=False)]
    try:value=json_answer(text)
    except (ValueError,TypeError,RecursionError):return [check('output_format','format','fail','要求完整JSON；存在额外正文或JSON格式无效。')]
    out=[check('output_format','format','pass','完整JSON格式已核对。')]
    if 'schema'in contract:
        from jsonschema import Draft202012Validator
        errors=list(Draft202012Validator(contract['schema']).iter_errors(value))
        out.append(check('output_schema','schema','fail'if errors else 'pass','JSON字段/类型不符合声明契约。'if errors else '声明JSON契约已核对。',
                         issues=[dict(path='/'+('/'.join(map(str,e.absolute_path))),message=e.message)for e in errors[:20]]))
    return out


def check_receipts(receipts,requirements):
    """Receipts must come from actual backend calls, never model-authored claims.

    Matching a command exit is deliberately separate from coverage/artifacts.
    Artifact/coverage producers own their scoped filesystem/test validation.
    """
    indexed={r['call_id']:r for r in receipts if isinstance(r,dict)and r.get('call_id')}
    out=[]
    for req in requirements:
        kind=req['kind'];ident=req['id'];receipt=indexed.get(req.get('receipt_id',req.get('call_id')))
        if receipt is None and kind=='artifact'and isinstance(req.get('path'),str):
            candidates=[r for r in receipts if r.get('tool')in{'project_read','workspace_read'}and r.get('arguments',{}).get('path')==req['path']]
            if req.get('environment'):candidates=[r for r in candidates if r.get('arguments',{}).get('environment')==req['environment']]
            if req.get('root'):candidates=[r for r in candidates if r.get('arguments',{}).get('root','main')==req['root']]
            scopes={(r['tool'],r.get('arguments',{}).get('environment')or r.get('arguments',{}).get('root','main'))for r in candidates}
            receipt=candidates[-1]if len(scopes)==1 else None
        if receipt is None and kind=='command'and isinstance(req.get('command'),str):
            receipt=next((r for r in reversed(receipts)if r.get('tool')in{'project_execute','workspace_run'}and r.get('arguments',{}).get('command')==req['command']),None)
        result=receipt.get('result')if receipt else None
        result=result if isinstance(result,dict)else {}
        status='unknown';message='缺少对应实际核验回执，未确认通过。'
        if kind=='command'and receipt:
            if type(req.get('exit_code',0))is not int:raise ValueError('expected exit code must be an integer')
            code=result.get('exit_code')
            if type(code)is int:
                status='pass'if code==req.get('exit_code',0)else 'fail';message='仅核对命令退出码，不代表测试覆盖或产物完整。'
        elif kind=='artifact'and receipt and receipt.get('tool')=='artifact_check':
            exists=result.get('exists')
            if type(exists)is bool:status='pass'if exists else 'fail';message='按受限产物检查器的真实回执核对。'
        elif kind=='artifact'and receipt and receipt.get('tool')in{'project_read','workspace_read'}:
            digest=result.get('sha256',result.get('revision'))
            if result.get('ok')is True and isinstance(digest,str)and re.fullmatch(r'[a-f0-9]{64}',digest):
                status='pass'if not req.get('sha256')or req['sha256']==digest else 'fail'
                message='对应文件的实际读取及内容摘要已核对；不独立证明文件满足全部功能。'
        elif kind=='test_coverage'and receipt and receipt.get('tool')=='test_coverage_check':
            covered=result.get('covered')
            if type(covered)is bool:status='pass'if covered else 'fail';message='按指定验证范围的实际检查回执核对。'
        out.append(check(ident,kind,status,message,receipt_id=receipt.get('call_id')if receipt else req.get('receipt_id',req.get('call_id'))))
    return out


def operation_fact(receipt):
    """Normalize only backend receipt contracts, not arbitrary prose or file data."""
    result=receipt.get('result');result=result if isinstance(result,dict)else {}
    operation=result.get('operation');operation=operation if isinstance(operation,dict)else {}
    saved=result.get('receipt');saved=saved if isinstance(saved,dict)else {}
    details=result.get('error_details');details=details if isinstance(details,dict)else {}
    ident=operation.get('id')or result.get('request_id')or saved.get('request_id')
    ident=ident if isinstance(ident,str)and ident else None
    state=operation.get('state');state=state if isinstance(state,str)else None
    changed=result.get('change_state');changed=changed if isinstance(changed,str)else None
    unclear=state in {'unknown','unconfirmed'}or changed=='unconfirmed'or details.get('outcome_unknown')is True
    if receipt.get('read_only')is False and receipt.get('status')=='failed'and not state and changed not in{'not_changed','committed'}:
        unclear=True
    resolved=not unclear and (state in {'succeeded','failed','cancelled','synced','saved','resolved_absent','retimed','observed','deleted','not_started'}
        or changed=='committed'or result.get('ok')is True and bool(saved.get('request_id')))
    return dict(key=ident or 'call:'+receipt['call_id'],id=ident,unclear=unclear,resolved=resolved,
                log_id=result.get('log_id')or operation.get('log_id'))


def pending_operations(receipts):
    unknown_operations=[];latest={}
    for receipt in receipts:
        fact=operation_fact(receipt)
        previous=latest.get(fact['key'])
        if not previous or fact['unclear']or fact['resolved']or not previous[1]['unclear']:
            latest[fact['key']]=(receipt,fact)
    attempted_keys={operation_fact(r)['key']for r in receipts if r.get('read_only')is False}
    for receipt,fact in latest.values():
        if fact['unclear']:
            unknown_operations.append(dict(call_id=receipt['call_id'],operation_id=fact['id'],log_id=fact['log_id'],
                state='unknown',action='reconcile_original_receipt_do_not_replay'))
            # A read-only query may truthfully return unknown. An actual write
            # attempt remains unconfirmed; final prose cannot turn it to success.
            unknown_operations[-1]['write_attempted']=fact['key']in attempted_keys
    return unknown_operations


def report(checks,*,execution='finished',receipts=()):
    checks=list(checks)
    unknown_operations=pending_operations(receipts)
    for operation in unknown_operations:
        if operation['write_attempted']:
            checks.append(check('unknown_operation_'+operation['call_id'],'task_acceptance','unknown','写入/执行结果仍未知，必须核对原操作，不能报完整完成。',operation_id=operation['operation_id']))
    required=[c for c in checks if c.get('required',True)]
    failed=any(c['status']=='fail'for c in required)
    unknown=any(c['status']=='unknown'for c in required)
    assessed=any(c['basis']in{'model_assessment','model_declaration'}for c in checks)
    completion='incomplete'if failed else 'unverified'if unknown or not required else 'assessed'if assessed else 'checked'
    return dict(version=1,execution=execution,completion=completion,ok=not(failed or unknown),checks=checks,unknown_operations=unknown_operations,
                scope='仅核对列明条件；模型审阅单独标识，不代表自动证明全部事实或全部自然语言目标。')


class RepairBudget:
    def __init__(self):self.used=False

    def begin(self):
        if self.used:return False
        self.used=True;return True

    def allows(self,name,spec):
        return not self.used or name=='build_chart'or spec.get('read_only')is True
