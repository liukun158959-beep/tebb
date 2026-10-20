"""Product text-only response contract; transport completion belongs to Atlas."""


def require_text(result):
    message = result.get('message') if isinstance(result, dict) else None
    if not isinstance(message, dict) or message.get('tool_calls') or message.get('function_call'):
        raise RuntimeError('模型返回了工具调用或无效消息，未保存为完整正文。')
    content = message.get('content')
    if type(content) is not str or not content.strip():
        raise RuntimeError('模型正文为空或不是字符串，未保存为完整正文。')
    return content.strip()
