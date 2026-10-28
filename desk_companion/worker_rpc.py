"""Authenticated worker access to existing parent-owned product services."""
import json
import os
from urllib.parse import quote

def call(method, args=None, timeout=120):
    from websockets.sync.client import connect
    port,token=os.environ.get('DESK_TERMINAL_PORT'),os.environ.get('DESK_TERMINAL_TOKEN')
    if not port or not token:
        raise RuntimeError('当前执行未连接父后台，不能执行产品操作。')
    with connect(f'ws://127.0.0.1:{int(port)}/ws?token={quote(token)}',open_timeout=5,close_timeout=1,proxy=None) as ws:
        ws.send(json.dumps(dict(type='rpc',method=method,args=args or {})))
        envelope=json.loads(ws.recv(timeout=timeout)).get('result',{})
        if not envelope.get('ok'):raise RuntimeError(envelope.get('error','后台调用失败'))
        return envelope['result']
