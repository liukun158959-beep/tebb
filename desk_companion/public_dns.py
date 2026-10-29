"""Resolve public hostnames behind Fake-IP DNS without connecting to fake IPs."""
import ipaddress
import json
import re
import time

from . import model_network

ENDPOINT = 'https://cloudflare-dns.com/dns-query'
FAKE_NET = ipaddress.ip_network('198.18.0.0/15')


def public_hostname(host):
    """Only real multi-label DNS names, never IP literals or local names."""
    try:
        host = host.rstrip('.').encode('idna').decode('ascii').lower()
    except UnicodeError:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return (len(host) <= 253 and '.' in host and not host.endswith(
        ('.localhost', '.local', '.internal', '.onion', '.test', '.invalid', '.home', '.lan'))
        and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in host.split('.'))
        and not host.split('.')[-1].isdigit())


def fake_addresses(host, addresses):
    return public_hostname(host) and bool(addresses) and all(ipaddress.ip_address(a) in FAKE_NET for a in addresses)


def _query(client, host, kind, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('公开 DNS 解析超时，未连接网页')
    with client.stream('GET', ENDPOINT, params={'name': host, 'type': kind},
                       headers={'Accept': 'application/dns-json'}, timeout=remaining,
                       follow_redirects=False) as response:
        if response.status_code != 200:
            raise ValueError(f'公开 DNS 解析失败：HTTP {response.status_code}，未连接网页')
        raw = bytearray()
        for chunk in response.iter_bytes():
            if time.monotonic() >= deadline:
                raise TimeoutError('公开 DNS 解析超时，未连接网页')
            raw.extend(chunk)
            if len(raw) > 65536:
                raise ValueError('公开 DNS 响应过大，未连接网页')
    if time.monotonic() >= deadline:
        raise TimeoutError('公开 DNS 解析超时，未连接网页')
    try:
        value = json.loads(raw)
        if (not isinstance(value, dict) or value.get('Status') != 0 or value.get('TC') is True
                or value.get('Question') not in ([{'name': host, 'type': kind}], [{'name': host + '.', 'type': kind}])):
            raise ValueError()
        records = value.get('Answer', [])
        if not isinstance(records, list) or len(records) > 64:
            raise ValueError()
        owners = {host}
        for _ in range(len(records)):
            for row in records:
                if row.get('type') == 5 and row.get('name', '').rstrip('.').lower() in owners:
                    alias = row['data'].rstrip('.').lower()
                    if not public_hostname(alias):
                        raise ValueError()
                    owners.add(alias)
        addresses = [str(ipaddress.ip_address(row['data'])) for row in records
                     if row.get('type') == kind and row.get('name', '').rstrip('.').lower() in owners]
        if any(not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).version != (4 if kind == 1 else 6)
               for address in addresses):
            raise ValueError()
        return list(dict.fromkeys(addresses))
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('公开 DNS 未返回可核对的公网地址，未连接网页') from None


def resolve(host, deadline):
    """Fixed HTTPS resolver receives only hostname/type, never page URL or credentials."""
    import httpx
    if not public_hostname(host):
        raise ValueError('网页读取不允许本机、内网或保留 IP')
    host = host.rstrip('.').encode('idna').decode('ascii').lower()
    deadline = min(deadline, time.monotonic() + 5)
    try:
        with model_network.http_client(max(.01, deadline-time.monotonic())) as client:
            addresses = _query(client, host, 1, deadline)
            if not addresses:
                addresses = _query(client, host, 28, deadline)
    except httpx.TransportError:
        raise ValueError('Fake-IP DNS 的 HTTPS 公网解析失败，请检查网络设置；未连接网页') from None
    if not addresses:
        raise ValueError('公开 DNS 没有公网地址，未连接网页')
    try:
        from .logutil import log
        log('web_dns ' + json.dumps(dict(resolver='cloudflare-doh', reason='fake_ip', host=host, addresses=len(addresses))))
    except OSError:
        pass
    return addresses
