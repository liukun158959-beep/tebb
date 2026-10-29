"""Offline, explicitly bound chat tokenizers; never guess a model's encoding."""
from functools import lru_cache
import json
from pathlib import Path
from threading import RLock

_LOCK = RLock()
_FILES = ('tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json',
          'chat_template.jinja', 'vocab.json', 'vocab.txt', 'merges.txt', 'tokenizer.model', 'config.json')


def binding(directory):
    if directory == '':
        return {}
    if not isinstance(directory, str) or len(directory) > 1000:
        raise ValueError('tokenizer 目录无效。')
    path = Path(directory).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError('请选择匹配模型的本地 tokenizer 目录。')
    if not (path/'tokenizer.json').is_file():
        raise ValueError('目录需包含官方 fast tokenizer.json；不加载慢速或自定义模型代码。')
    signature = [(name, p.stat().st_size, p.stat().st_mtime_ns)
                 for name in _FILES if (p := path / name).is_file()]
    if not signature or sum(size for _, size, _ in signature) > 150_000_000:
        raise ValueError('tokenizer 资料缺失或超过150MB。不要选择模型权重目录代替分词资料。')
    result = dict(tokenizer_dir=str(path), tokenizer_signature=signature)
    tokenizer = _load(str(path), json.dumps(signature))
    if not tokenizer.chat_template:
        raise ValueError('tokenizer 缺少聊天模板，不能统计完整消息。')
    return result


@lru_cache(maxsize=4)
def _load(directory, signature):
    from transformers import PreTrainedTokenizerFast
    return PreTrainedTokenizerFast.from_pretrained(directory, local_files_only=True, trust_remote_code=False)


def count(request, config):
    result = dict(tokens=None, source='unavailable', note='未绑定匹配模型的 tokenizer。')
    directory = config.get('tokenizer_dir')
    if not directory:
        return result
    try:
        path = Path(directory)
        signature = [(name, p.stat().st_size, p.stat().st_mtime_ns)
                     for name in _FILES if (p := path / name).is_file()]
        if json.dumps(signature) != json.dumps(config.get('tokenizer_signature')):
            raise ValueError('tokenizer 文件发生变化，请重新确认绑定。')
        messages = request.get('messages')
        if not isinstance(messages, list) or not messages:
            raise ValueError('请求没有可统计的聊天消息。')
        if any(not isinstance(m, dict) or not isinstance(m.get('content', ''), (str, type(None))) for m in messages):
            raise ValueError('本地计数不覆盖图片、音频或其他非文本消息。')
        if len(json.dumps(request, ensure_ascii=False)) > 2_000_000:
            raise ValueError('请求过大，未执行本地计数。')
        with _LOCK:
            tokenizer = _load(directory, json.dumps(signature))
            template = tokenizer.get_chat_template()
            if request.get('tools') and 'tools' not in template:
                raise ValueError('所选聊天模板不支持工具定义，不能给出完整计数。')
            args = dict(tokenize=True, add_generation_prompt=True, return_dict=False)
            if request.get('tools'):
                args['tools'] = request['tools']
            # Forward template options only; HTTP sampling fields are not prompt text.
            for key in ('enable_thinking', 'reasoning_effort'):
                if key in request:
                    args[key] = request[key]
            tokens = tokenizer.apply_chat_template(messages, **args)
        return dict(tokens=len(tokens), source='tokenizer', note='绑定的 tokenizer 与聊天模板实算；服务端模板不同可能产生差异。')
    except Exception as exc:
        return {**result, 'note': '本地分词不可用：' + str(exc)[:300]}
