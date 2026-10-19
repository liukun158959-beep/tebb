"""Model-only network policy. Never change the process/system proxy environment."""
import json
import os
import ssl
from urllib.parse import urlsplit

from .paths import data_root

DEFAULT = dict(mode='system', proxy_url='', username='', password='')


def path():
    return data_root() / 'model-network.json'


def validate(raw):
    if not isinstance(raw, dict) or raw.get('mode') not in {'system', 'direct', 'custom'}:
        raise ValueError('请选择系统代理、直连或自定义代理。')
    cfg = {k: raw.get(k, '') for k in DEFAULT}
    if any(not isinstance(v, str) or any(c in v for c in '\r\n') for v in cfg.values()):
        raise ValueError('代理配置格式无效。')
    cfg['proxy_url'] = cfg['proxy_url'].strip().rstrip('/')
    if cfg['proxy_url']:
        try:
            parsed = urlsplit(cfg['proxy_url']); port = parsed.port
            valid = parsed.scheme in {'http', 'https', 'socks5', 'socks5h'} and parsed.hostname and not (
                parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or any(c.isspace() for c in cfg['proxy_url']))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('代理地址须为 http://、https:// 或 socks5:// 主机和端口，认证请填单独的账号密码。')
    if cfg['mode'] == 'custom' and not cfg['proxy_url']:
        raise ValueError('自定义代理需要填写地址。')
    return cfg


def load():
    return validate({**DEFAULT, **json.loads(path().read_text('utf-8'))}) if path().exists() else DEFAULT.copy()


def public(cfg=None):
    cfg = load() if cfg is None else cfg
    return {k:v for k,v in cfg.items() if k != 'password'} | dict(has_password=bool(cfg['password']))


def save(payload):
    if not isinstance(payload, dict): raise ValueError('代理配置必须是对象。')
    current = load()
    password = payload.get('password')
    changed = ('proxy_url' in payload and str(payload['proxy_url']).strip().rstrip('/') != current['proxy_url']) or ('username' in payload and payload['username'] != current['username'])
    if payload.get('clear_password') is True or changed and not password: password = ''
    elif not password: password = current['password']
    cfg = validate({**current, **{k:v for k,v in payload.items() if k in DEFAULT}, 'password':password})
    from .video import atomic_write
    atomic_write(path(), cfg)
    return dict(ok=True, **public(cfg))


def http_client(timeout=90, policy=None):
    import certifi
    import httpx
    cfg = validate(policy) if policy is not None else load()
    kwargs = dict(timeout=httpx.Timeout(timeout, connect=min(5, timeout)), trust_env=cfg['mode']=='system')
    if cfg['mode'] != 'system':
        # trust_env=False also disables CA environment variables in HTTPX. Preserve
        # explicitly configured trust roots while disabling only proxy discovery.
        kwargs['verify'] = ssl.create_default_context(cafile=os.getenv('SSL_CERT_FILE') or certifi.where(), capath=os.getenv('SSL_CERT_DIR'))
    if cfg['mode'] == 'custom':
        kwargs['proxy'] = httpx.Proxy(cfg['proxy_url'], auth=(cfg['username'], cfg['password']) if cfg['username'] else None)
    client = httpx.Client(**kwargs)
    client._tebb_network_mode = cfg['mode']
    return client


def openai_client(api_key, base_url, timeout=90):
    from openai import OpenAI
    client = http_client(timeout)
    return OpenAI(api_key=api_key, base_url=base_url, timeout=client.timeout, max_retries=0, http_client=client)
