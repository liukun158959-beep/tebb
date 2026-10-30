"""Bounded workflow graph and value references; no IO or execution policy."""
from __future__ import annotations

import copy
import hashlib
import json
import re

KINDS = {
    'trigger': ('触发', {'text': ''}, ('text',)),
    'search': ('搜索', {'query': '', 'limit': 3}, ('text', 'urls', 'first_url')),
    'read': ('读取网页', {'url': ''}, ('text', 'url', 'title', 'truncated')),
    'ai': ('AI 处理', {'prompt': '', 'model_id': ''}, ('text',)),
    'condition': ('条件', {'left': '', 'operator': 'not_empty', 'right': ''}, ('result',)),
    'document': ('创建飞书文档', {'title': '', 'content': '', 'parent_token': '', 'profile': ''}, ('document_id', 'url')),
    'base': ('写多维表格', {'base_token': '', 'table_id': '', 'fields': {}, 'key_field': '', 'profile': ''}, ('record_id',)),
    'message': ('发送群消息', {'chat_id': '', 'text': '', 'profile': ''}, ('message_id',)),
}
WRITES = frozenset({'document', 'base', 'message'})
IDENT = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,63}\Z')
REF = re.compile(r'\{\{\s*([A-Za-z][A-Za-z0-9_-]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}')


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def validate(graph):
    if not isinstance(graph, dict) or set(graph) - {'nodes', 'edges'}:
        raise ValueError('工作流须包含 nodes 和 edges。')
    nodes, edges = graph.get('nodes'), graph.get('edges')
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 32 or not isinstance(edges, list) or len(edges) > 64:
        raise ValueError('工作流需要 1–32 个节点，连线不超过 64 条。')
    result, by_id = {'nodes': [], 'edges': []}, {}
    for node in nodes:
        if not isinstance(node, dict) or set(node) - {'id', 'kind', 'config', 'position'}:
            raise ValueError('节点结构无效。')
        ident, kind, config = node.get('id'), node.get('kind'), node.get('config', {})
        if not isinstance(ident, str) or not IDENT.fullmatch(ident) or ident in by_id or kind not in KINDS:
            raise ValueError('节点编号须唯一，且节点类型须来自能力目录。')
        defaults = KINDS[kind][1]
        if not isinstance(config, dict) or set(config) - set(defaults):
            raise ValueError(f'{ident} 含未知配置。')
        config = {**copy.deepcopy(defaults), **copy.deepcopy(config)}
        for key, default in defaults.items():
            if type(config[key]) is not type(default):
                raise ValueError(f'{ident}.{key} 类型错误。')
        if any(len(text) > 50000 for text in _strings(config)):
            raise ValueError('节点单项文本不能超过 50000 字。')
        if kind == 'search' and not 1 <= config['limit'] <= 5:
            raise ValueError('每个搜索节点最多返回 5 条来源。')
        if kind == 'condition' and config['operator'] not in {'equals', 'contains', 'not_empty'}:
            raise ValueError('条件只支持等于、包含、非空。')
        required = {'search': ['query'], 'read': ['url'], 'ai': ['prompt'], 'document': ['title', 'content'],
                    'message': ['chat_id', 'text'], 'base': ['base_token', 'table_id', 'key_field']}.get(kind, [])
        if any(not config[key].strip() for key in required):
            raise ValueError(f'{ident} 缺少必填配置：{", ".join(required)}。')
        if kind == 'base':
            if not config['fields'] or len(config['fields']) > 30 or any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) for k, v in config['fields'].items()):
                raise ValueError('多维表格首版支持最多 30 个普通文本字段。')
            if config['key_field'] in config['fields']:
                raise ValueError('去重字段由工作流写入稳定回执编号，不要同时填写。')
        # Targets/profile cannot be changed by an upstream model response.
        for key in ('chat_id', 'base_token', 'table_id', 'key_field', 'parent_token', 'profile', 'model_id'):
            if key in config and ('{{' in config[key] or len(config[key]) > 300):
                raise ValueError(f'{ident}.{key} 必须是确认过的固定目标，不能引用动态输出。')
        position = node.get('position', {'x': 0, 'y': 0})
        if not isinstance(position, dict) or set(position) != {'x', 'y'} or any(type(v) not in {int, float} or not -100000 <= v <= 100000 for v in position.values()):
            raise ValueError('节点位置无效。')
        clean = dict(id=ident, kind=kind, config=config, position=position)
        result['nodes'].append(clean)
        by_id[ident] = clean
    triggers = [n['id'] for n in result['nodes'] if n['kind'] == 'trigger']
    if len(triggers) != 1:
        raise ValueError('每个工作流必须恰有一个触发节点。')
    incoming, outgoing, seen = {k: [] for k in by_id}, {k: [] for k in by_id}, set()
    for edge in edges:
        if not isinstance(edge, dict) or set(edge) - {'source', 'target', 'branch'}:
            raise ValueError('连线结构无效。')
        source, target, branch = edge.get('source'), edge.get('target'), edge.get('branch', '')
        if source not in by_id or target not in by_id or source == target or target == triggers[0]:
            raise ValueError('连线引用无效或指向触发节点。')
        if branch not in ({'true', 'false'} if by_id[source]['kind'] == 'condition' else {''}):
            raise ValueError('条件连线须选择 true/false，其余连线不设置分支。')
        key = source, target, branch
        if key in seen:
            raise ValueError('存在重复连线。')
        seen.add(key)
        clean = dict(source=source, target=target, branch=branch)
        result['edges'].append(clean)
        incoming[target].append(clean)
        outgoing[source].append(clean)
    queue, order, ancestors = list(triggers), [], {k: set() for k in by_id}
    degree = {k: len(v) for k, v in incoming.items()}
    while queue:
        current = queue.pop(0)
        order.append(current)
        for edge in outgoing[current]:
            target = edge['target']
            ancestors[target] |= ancestors[current] | {current}
            degree[target] -= 1
            if degree[target] == 0:
                queue.append(target)
    if len(order) != len(nodes):
        raise ValueError('工作流不能有循环或未连接到触发节点的节点。')
    for node in result['nodes']:
        for text in _strings(node['config']):
            if '{{' in REF.sub('', text) or '}}' in REF.sub('', text):
                raise ValueError('引用格式应为 {{节点编号.输出字段}}。')
            for source, field in REF.findall(text):
                if source not in ancestors[node['id']] or field not in KINDS[by_id[source]['kind']][2]:
                    raise ValueError(f'{node["id"]} 引用了非上游节点或不存在的输出 {source}.{field}。')
    if len(json.dumps(result, ensure_ascii=False).encode()) > 500000:
        raise ValueError('工作流配置超过 500 KiB。')
    return result, order


def version(graph):
    clean, _ = validate(graph)
    return hashlib.sha256(json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def resolve(value, outputs):
    if isinstance(value, dict):
        return {k: resolve(v, outputs) for k, v in value.items()}
    if not isinstance(value, str):
        return value
    def replace(match):
        source, field = match.groups()
        if source not in outputs or field not in outputs[source]:
            raise ValueError(f'上游输出 {source}.{field} 不可用，未执行当前节点。')
        item = outputs[source][field]
        return item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
    return REF.sub(replace, value)


def ready_node(graph, order, receipts):
    by_id = {n['id']: n for n in graph['nodes']}
    for ident in order:
        if receipts.get(ident, {}).get('state') in {'confirmed', 'skipped'}:
            continue
        incoming = [e for e in graph['edges'] if e['target'] == ident]
        if any(receipts.get(e['source'], {}).get('state') not in {'confirmed', 'skipped'} for e in incoming):
            continue
        active = not incoming or any(receipts[e['source']]['state'] == 'confirmed' and
            (not e['branch'] or str(receipts[e['source']]['output']['result']).lower() == e['branch']) for e in incoming)
        return by_id[ident], active
    return None, False
