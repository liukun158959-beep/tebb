"""Search providers own search transport; chat credentials never reach public sites."""
from __future__ import annotations

import json
import re
import time
import uuid
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urlsplit

import httpx

from . import model_network
from .paths import data_root

EXA = 'https://mcp.exa.ai/mcp'
LIMIT = 5
MAX_BODY = 1_000_000
TOTAL_SECONDS = 30


class SearchFailure(RuntimeError):
    def __init__(self, message, *, category='response_error', stage='response', http_status=None, retry_after=0):
        super().__init__(message)
        self.category, self.stage, self.http_status = category, stage, http_status
        self.retry_after = retry_after
        self.attempts = []

    def diagnostic(self):
        return dict(category=self.category, stage=self.stage, http_status=self.http_status)


def search_endpoint(base_url):
    url=(base_url or '').strip().rstrip('/')
    if not url.endswith('/v1'):
        raise SearchFailure('仅明确支持模型私有搜索时，API地址须以 /v1 结尾。',category='configuration',stage='admission')
    return url+'/tool/search'


def settings():
    path = data_root() / 'search.json'
    value = json.loads(path.read_text('utf-8')) if path.exists() else {'provider': 'exa'}
    if value.get('provider') not in {'exa', 'model-api'}:
        raise ValueError('搜索渠道配置无效，请在设置重新选择。')
    return dict(provider=value['provider'], fallback='bing-rss', timeout_s=TOTAL_SECONDS)


def save_settings(payload):
    if not isinstance(payload, dict) or payload.get('provider') not in {'exa', 'model-api'}:
        raise ValueError('请选择 Exa 或明确支持搜索的模型私有接口。')
    from .video import atomic_write
    atomic_write(data_root() / 'search.json', {'provider': payload['provider']})
    return dict(ok=True, **settings())


def _cooldown(provider, seconds=None):
    """Share only provider health, never queries or credentials, across workers."""
    from .resource_lock import ResourceLock
    from .video import atomic_write
    path = data_root() / 'search-health.json'
    with ResourceLock('search-health'):
        try:
            values = json.loads(path.read_text('utf-8')) if path.exists() else {}
        except (OSError, ValueError):
            values = {}
        if seconds is not None:
            values[provider] = time.time() + max(1, min(900, seconds))
            atomic_write(path, values)
        return max(0, float(values.get(provider, 0)) - time.time())


def _retry_after(raw):
    try:
        return max(1, min(900, int(raw)))
    except (ValueError, TypeError):
        try:
            return max(1, min(900, parsedate_to_datetime(raw).timestamp() - time.time()))
        except (ValueError, TypeError, OverflowError):
            return 30


def _remaining(deadline, cancelled=None):
    if cancelled and cancelled():
        raise SearchFailure('搜索已取消。', category='cancelled', stage='budget')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SearchFailure('搜索总时限已用完。', category='timeout', stage='budget')
    return remaining


def _request(client, method, url, deadline, *, cancelled=None, **kwargs):
    remaining = _remaining(deadline, cancelled)
    stage = 'connect_or_response_headers'
    try:
        with client.stream(method, url, timeout=httpx.Timeout(min(10, remaining), connect=min(5, remaining)), **kwargs) as response:
            if response.status_code >= 400:
                status = response.status_code
                category = ('unsupported' if status in {404, 405, 501} else 'rate_limit' if status == 429
                            else 'authentication' if status in {401, 403} else 'temporary' if status in {408, 500, 502, 503, 504} else 'http_error')
                raise SearchFailure(f'搜索服务 HTTP {status}。', category=category, stage='response_headers', http_status=status,
                                    retry_after=_retry_after(response.headers.get('retry-after')) if status == 429 else 0)
            stage = 'response_body'
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                _remaining(deadline, cancelled)
                size += len(chunk)
                if size > MAX_BODY:
                    raise SearchFailure('搜索响应超过大小限制。', category='response_size', stage=stage)
                chunks.append(chunk)
            _remaining(deadline, cancelled)
            return b''.join(chunks).decode('utf-8'), response.headers.get('content-type', '')
    except httpx.TimeoutException:
        raise SearchFailure('搜索网络超时。', category='timeout', stage=stage) from None
    except httpx.TransportError as exc:
        # Never include raw exception URLs, proxy credentials or model keys.
        certificate = 'CERTIFICATE_VERIFY_FAILED' in str(exc).upper() or 'certificate verify' in str(exc).lower()
        raise SearchFailure('搜索证书校验失败。' if certificate else '搜索连接或传输中断。',
                            category='tls_certificate' if certificate else 'connection_error', stage=stage) from None


def _rpc(raw, content_type, ident):
    try:
        if 'text/event-stream' in content_type or raw.lstrip().startswith(('event:', 'data:', ':')):
            frames = re.split(r'\r?\n\r?\n', raw)
            values = [json.loads('\n'.join(line[5:].lstrip(' ') for line in frame.splitlines() if line.startswith('data:')))
                      for frame in frames if any(line.startswith('data:') for line in frame.splitlines())]
        else:
            values = [json.loads(raw)]
        value = next(v for v in values if isinstance(v, dict) and v.get('id') == ident)
    except (ValueError, StopIteration, TypeError):
        raise SearchFailure('搜索 MCP 响应不完整或格式无效。', category='protocol') from None
    result = value.get('result')
    if value.get('jsonrpc')!='2.0':raise SearchFailure('搜索 MCP 协议版本无效。',category='protocol')
    if isinstance(result,dict) and isinstance(result.get('_meta'),dict) and result['_meta'].get('ai.exa/rateLimited') is True:
        raise SearchFailure('Exa 公开搜索达到限流，稍后核对或使用备用渠道。',category='rate_limit',retry_after=30)
    if value.get('error') or not isinstance(result, dict) or result.get('isError'):
        raise SearchFailure('搜索 MCP 返回业务错误。', category='upstream_error')
    return result


def _exa(client, query, deadline, cancelled):
    ident = uuid.uuid4().hex
    raw, content_type = _request(client, 'POST', EXA, deadline, cancelled=cancelled,
        headers={'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json'},
        json={'jsonrpc': '2.0', 'id': ident, 'method': 'tools/call', 'params': {
            'name': 'web_search_exa', 'arguments': {'query': query, 'objective': 'Find relevant original sources for: ' + query, 'numResults': LIMIT}}})
    result = _rpc(raw, content_type, ident)
    content=result.get('content')
    if not isinstance(content,list):raise SearchFailure('搜索 MCP 内容结构无效。',category='protocol')
    text = '\n'.join(block['text'] for block in content if isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'),str))
    rows = []
    for block in re.split(r'(?m)(?=^Title: )', text):
        title = re.search(r'^Title: (.+)$', block, re.M)
        link = re.search(r'^URL: (https?://\S+)', block, re.M)
        if not title or not link:
            continue
        published = re.search(r'^Published: (.+)$', block, re.M)
        stamp = published.group(1).strip() if published else ''
        rows.append(dict(title=title.group(1), link=link.group(1), snippet=block.split('Highlights:', 1)[-1][:3000] if 'Highlights:' in block else '',
                         published_date=stamp if stamp not in {'N/A', 'Unknown', 'null'} else ''))
    return rows


def _bing(client, query, deadline, cancelled):
    raw, _ = _request(client, 'GET', 'https://www.bing.com/search?' + urlencode({'format': 'rss', 'q': query}), deadline,
                      cancelled=cancelled, headers={'User-Agent': 'tebb-search/0.3', 'Accept': 'application/rss+xml, application/xml'})
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise SearchFailure('备用搜索响应无效。', category='protocol') from None
    return [dict(title=row.findtext('title', ''), link=row.findtext('link', ''), snippet=row.findtext('description', ''),
                 published_date=row.findtext('pubDate', '')) for row in root.findall('./channel/item')]


def _private(client, query, deadline, cancelled):
    from .model_catalog import require_active
    model = require_active()
    raw, _ = _request(client, 'POST', search_endpoint(model['base_url']), deadline, cancelled=cancelled,
                      headers={'Authorization': 'Bearer ' + model['api_key']},
                      json=dict(query=query, max_results=LIMIT, crawl_results=0, search_service='google'))
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or not isinstance(value.get('results'), list):
            raise ValueError()
        return value['results']
    except ValueError:
        raise SearchFailure('私有搜索返回结构无效。', category='protocol') from None


def _normalize(rows):
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        link, title = row.get('link'), row.get('title')
        try:
            parsed = urlsplit(link) if isinstance(link, str) else None
            valid = parsed and parsed.scheme in {'http', 'https'} and parsed.hostname and not (parsed.username or parsed.password)
        except ValueError:
            valid = False
        if not valid or not isinstance(title, str) or not title.strip() or link in seen:
            continue
        seen.add(link)
        snippet=str(row.get('snippet') or '')[:3000]
        if sum(c=='\ufffd' or ord(c)<32 and c not in '\n\r\t' for c in snippet)>max(2,len(snippet)*.02):snippet=''
        result.append(dict(title=title.strip()[:500], link=link, snippet=snippet,
                           published_date=str(row.get('published_date') or '')[:80]))
        if len(result) == LIMIT:
            break
    if not result:
        raise SearchFailure('搜索未返回可用来源。', category='empty')
    return result


def _search(query, *, on_status=None, cancelled=None, budget_s=TOTAL_SECONDS):
    if not isinstance(query, str) or not query.strip() or len(query.strip()) > 200:
        raise ValueError('搜索词须为1至200字。')
    query = query.strip()
    provider = settings()['provider']
    started = time.monotonic()
    deadline = started + min(TOTAL_SECONDS, max(.1, budget_s))
    attempts = []
    failure = None
    with model_network.http_client(min(10, budget_s)) as client:
        for channel in (provider, 'bing-rss'):
            if _cooldown(channel):
                failure = SearchFailure('搜索渠道暂处冷却期。', category='cooldown', stage='admission')
                attempts.append(dict(provider=channel, attempt=0, ok=False, elapsed_ms=0, **failure.diagnostic()))
                continue
            for attempt in range(2 if channel != 'bing-rss' else 1):
                mark = time.monotonic()
                try:
                    _remaining(deadline, cancelled)
                    rows = {'exa': _exa, 'model-api': _private, 'bing-rss': _bing}[channel](client, query, deadline, cancelled)
                    rows = _normalize(rows)
                    attempts.append(dict(provider=channel, attempt=attempt + 1, ok=True, elapsed_ms=int((time.monotonic() - mark) * 1000)))
                    return dict(results=rows, provider=channel, attempts=attempts, elapsed_ms=int((time.monotonic() - started) * 1000),
                                **(dict(fallback_reason=failure.category, primary_failure=failure.diagnostic()) if failure else {}))
                except SearchFailure as exc:
                    failure = exc
                    attempts.append(dict(provider=channel, attempt=attempt + 1, ok=False, elapsed_ms=int((time.monotonic() - mark) * 1000), **exc.diagnostic()))
                    if exc.category in {'cancelled', 'authentication', 'tls_certificate'}:
                        exc.attempts = attempts
                        raise
                    if exc.category in {'rate_limit', 'unsupported'}:
                        _cooldown(channel, exc.retry_after if exc.category == 'rate_limit' else 300)
                    if attempt == 0 and channel != 'bing-rss' and exc.category in {'connection_error', 'timeout', 'temporary'} and deadline - time.monotonic() > 12:
                        if on_status:
                            on_status('搜索临时故障，重试一次。')
                        continue
                    break
            if on_status and channel != 'bing-rss':
                on_status('主搜索未取得来源，切换公共 Bing 备用；仍需核对相关性。')
    error = SearchFailure('所有搜索渠道均未取得来源，请查看搜索诊断后重试。', category='all_providers_failed', stage='fallback')
    error.attempts = attempts
    raise error


def search(query, **options):
    """Persist safe transport diagnostics, independent of each caller's journal."""
    diagnostic={}
    try:
        result=_search(query,**options)
        diagnostic=dict(ok=True,provider=result['provider'],attempts=result['attempts'],elapsed_ms=result['elapsed_ms'])
        return result
    except SearchFailure as exc:
        diagnostic=dict(ok=False,attempts=exc.attempts,**exc.diagnostic())
        raise
    finally:
        if diagnostic:
            try:
                from .logutil import log
                log('search '+json.dumps(diagnostic,ensure_ascii=False,separators=(',',':')))
            except OSError:pass  # Observability cannot change an obtained result.


def probe(query='Python asyncio TaskGroup official documentation'):
    try:
        return dict(ok=True, **search(query))
    except SearchFailure as exc:
        return dict(ok=False, error=str(exc), attempts=exc.attempts, **exc.diagnostic())
