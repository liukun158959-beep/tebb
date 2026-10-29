"""Read-only search + article smoke test in isolated data, without a model."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desk_companion import model_network, search_service, web_sources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--repeat', type=int, default=2)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not args.live or not 1 <= args.repeat <= 3:
        parser.error('Requires --live and --repeat 1..3 (external read-only requests).')
    policy = model_network.load()
    records = []
    previous = os.environ.get('DESK_COMPANION_DATA_DIR')
    try:
        with tempfile.TemporaryDirectory() as folder:
            os.environ['DESK_COMPANION_DATA_DIR'] = folder
            Path(folder, 'model-network.json').write_text(json.dumps(policy), encoding='utf-8')
            for repeat in range(args.repeat):
                for query in ['AI 最新技术资讯 2026年10月 官方', 'KIMI 3.1 模型 官方 发布']:
                    result = search_service.probe(query)
                    record = dict(query=query, repeat=repeat+1, search=result, pages=[])
                    records.append(record)
                    for row in result.get('results', [])[:3]:
                        url = row['link']; started = time.monotonic()
                        page = dict(url=url)
                        try:
                            page['system_addresses'] = sorted({a[4][0] for a in socket.getaddrinfo(
                                web_sources.urlsplit(url).hostname, 443, type=socket.SOCK_STREAM)})
                            body = web_sources.fetch_page(url)
                            page.update(ok=True, **{k: body[k] for k in ('url', 'title', 'level', 'characters', 'truncated', 'sha256')},
                                        preview=body['text'][:800])
                        except Exception as exc:
                            page.update(ok=False, error=type(exc).__name__+': '+str(exc))
                        page['elapsed_ms'] = round((time.monotonic()-started)*1000)
                        record['pages'].append(page)
                        print(repeat+1, 'body' if page['ok'] else 'failed', url, page.get('characters', page.get('error')), flush=True)
    finally:
        if previous is None: os.environ.pop('DESK_COMPANION_DATA_DIR', None)
        else: os.environ['DESK_COMPANION_DATA_DIR'] = previous
    pages = [p for r in records for p in r['pages']]
    report = dict(searches=len(records), searches_ok=sum(bool(r['search']['ok']) for r in records),
                  pages=len(pages), pages_ok=sum(p['ok'] for p in pages), cases=records,
                  scope='Network/body availability only; does not establish relevance, timeliness, or model claims.')
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print({k:v for k,v in report.items() if k!='cases'}, flush=True)


if __name__=='__main__': main()
