"""One sequential, resumable workflow attempt inside an existing task worker."""
from __future__ import annotations

import json

from .workflow_graph import KINDS, WRITES, ready_node, resolve, validate
from .workflows import WorkflowStore
from .task_budgets import current


def execute(request, emit, *, store=None, adapter=None, check_target=None):
    from .workflow_nodes import WriteRejected, execute as node_execute, preflight
    store = store or WorkflowStore()
    adapter, check_target = adapter or node_execute, check_target or preflight
    ident, attempt = request['workflow_run'], request['workflow_attempt']
    if not store.claim(ident, attempt):
        emit('failure', '该执行已由其他进程取得或状态已变化，未重复执行。')
        return
    try:
        run = store.run(ident)
        graph, order = validate(run['graph'])
        request = {**request, 'workflow_input': run['input']}
        while True:
            run = store.run(ident)
            if run['attempt'] != attempt or run['state'] != 'running':
                emit('failure', '执行所有权已变化，原工作进程停止。')
                return
            node, active = ready_node(graph, order, run['receipts'])
            if node is None:
                store.set_run(ident, attempt=attempt, require_running=True, state='completed', error='')
                if current():current().ledger.finish(current().member['id'])
                emit('result', '工作流已完成，结果与各节点回执已保存。')
                return
            if not active:
                store.receipt(ident, node, 'skipped', attempt=attempt)
                continue
            previous = run['receipts'].get(node['id'], {})
            if previous.get('state') in {'unknown', 'pending'}:
                store.set_run(ident, attempt=attempt, require_running=True, state='unknown', error='原写入结果待核对，未重放。')
                if current():current().ledger.waiting(current().member['id'],True)
                emit('failure', '原节点回执待核对。')
                return
            if node['kind'] in WRITES and node['id'] not in run['approved']:
                store.set_run(ident, attempt=attempt, require_running=True, state='waiting_approval', error='确认该节点的飞书目标和内容后才能执行。')
                if current():current().ledger.waiting(current().member['id'],True)
                emit('result', '已停在写入节点，等待明确确认目标与内容。')
                return
            outputs = {key: r['output'] for key, r in run['receipts'].items() if r['state'] == 'confirmed'}
            inputs = resolve(node['config'], outputs)
            if current():current().tool(node['kind']not in WRITES,'workflow_'+node['kind'])
            # Preflight is read-only and outside storage transactions.
            if node['kind'] in WRITES:
                check_target(node['kind'], inputs)
            store.receipt(ident, node, 'pending', inputs=inputs, attempt=attempt)
            receipt = store.run(ident)['receipts'][node['id']]
            emit('status', KINDS[node['kind']][0] + ' · ' + node['id'])
            emit('tool_start', dict(tool='workflow_' + node['kind'], node_id=node['id'], read_only=node['kind'] not in WRITES,
                                    input=inputs, fingerprint=receipt['receipt_key']))
            try:
                from .usage_observer import invoke_tool
                output = invoke_tool('workflow_'+node['kind'],adapter,node['kind'],inputs,receipt['receipt_key'],emit,request)
                if not isinstance(output, dict) or set(output) != set(KINDS[node['kind']][2]) or len(json.dumps(output, ensure_ascii=False).encode()) > 500000:
                    raise ValueError('节点未返回完整、有效的输出。')
                store.receipt(ident, node, 'confirmed', output=output, attempt=attempt)
                if current():
                    import hashlib
                    current().progress('workflow_node',ident+':'+node['id'],hashlib.sha256(json.dumps(output,sort_keys=True).encode()).hexdigest())
                emit('tool_end', dict(tool='workflow_' + node['kind'], node_id=node['id'], status='success', result=output,
                                      read_only=node['kind'] not in WRITES, fingerprint=receipt['receipt_key']))
            except Exception as exc:
                unknown = node['kind'] in WRITES and not isinstance(exc, WriteRejected)
                error = str(exc) if isinstance(exc, (ValueError, WriteRejected)) else f'节点执行未完成（{type(exc).__name__}），请查看调用记录并核对原回执。'
                store.receipt(ident, node, 'unknown' if unknown else 'failed', error=error, attempt=attempt)
                store.set_run(ident, attempt=attempt, require_running=True, state='unknown' if unknown else 'failed', error=error)
                if current():current().ledger.waiting(current().member['id'],True)
                emit('tool_end', dict(tool='workflow_' + node['kind'], node_id=node['id'], status='failed', error=error,
                                      read_only=node['kind'] not in WRITES, fingerprint=receipt['receipt_key']))
                emit('failure', error)
                return
            if run['mode'] == 'step':
                store.set_run(ident, attempt=attempt, require_running=True, state='paused', error='')
                if current():current().ledger.waiting(current().member['id'],True)
                emit('result', '当前步骤已完成，继续前可检查输入和输出。')
                return
    except Exception as exc:
        # An error persisting a successful write must not turn it into a retryable failure.
        snapshot = store.run(ident)
        unknown = any(r['state'] == 'pending' and r['kind'] in WRITES for r in snapshot['receipts'].values())
        store.set_run(ident, attempt=attempt, require_running=True, state='unknown' if unknown else 'failed', error=f'工作流未完成（{type(exc).__name__}）。请核对节点回执。')
        if current():current().ledger.waiting(current().member['id'],True)
        emit('failure', f'工作流未完成（{type(exc).__name__}），未自动重放。')
