"""User-declared model window budgets for observation, never API overrides."""
import json

from .paths import data_root


def load(model_id):
    path=data_root()/'model-capacity.json'
    values=json.loads(path.read_text('utf-8')) if path.exists() else {}
    return values.get(model_id,{})


def save(model_id,window=None,output_reserve=0,tokenizer_dir=None):
    from .model_catalog import require_item
    from .resource_lock import ResourceLock
    from .video import atomic_write
    model=require_item(model_id)
    if window is not None and (type(window) is not int or not 1024<=window<=10_000_000):
        raise ValueError('窗口总量须为1024至10000000的整数，未知时留空。')
    if type(output_reserve) is not int or output_reserve<0 or window is None and output_reserve or window is not None and output_reserve>=window:
        raise ValueError('输出预留须为非负整数且小于窗口总量；未知容量时预留为0。')
    from .model_tokenizer import binding
    tokenizer=binding(tokenizer_dir) if tokenizer_dir is not None else None
    path=data_root()/'model-capacity.json'
    with ResourceLock('model-capacity'):
        values=json.loads(path.read_text('utf-8')) if path.exists() else {}
        previous=values.get(model_id,{})
        keep={k:v for k,v in previous.items() if k.startswith('tokenizer_')} if previous.get('model')==model['model'] and previous.get('base_url')==model['base_url'] else {}
        values[model_id]=dict(window=window,output_reserve=output_reserve,model=model['model'],base_url=model['base_url'],**(keep if tokenizer is None else tokenizer))
        atomic_write(path,values)
    return dict(ok=True,capacity=values[model_id])


def for_call(model_id,model,endpoint):
    value=load(model_id)
    base=value.get('base_url','').rstrip('/')
    if value.get('model')!=model or not base or not endpoint.startswith(base+'/'):
        return dict(window=None,output_reserve=0,source='unknown')
    return {k:v for k,v in value.items() if k in {'window','output_reserve'} or k.startswith('tokenizer_')}|dict(source='user_config')
