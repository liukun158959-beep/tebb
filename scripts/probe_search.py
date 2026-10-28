"""Explicit live search evaluation. No chat/model call or persistent user changes."""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desk_companion import model_network, search_service

CASES = [
    ('中文技术', 'Python TaskGroup 官方文档 结构化并发', ['python.org']),
    ('英文技术', 'React useEffect cleanup official documentation', ['react.dev']),
    ('中文实体', '飞书 开放平台 日历 日程 更新 API', ['feishu.cn']),
    ('英文实体', 'Anthropic Claude Code subagents documentation', ['claude.com', 'anthropic.com']),
    ('中文时效', '中国科学院 2026年10月 科研 新闻', ['cas.cn']),
    ('英文时效', 'OpenAI October 2026 latest announcements official', ['openai.com']),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='Allow read-only external search requests')
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not args.live: parser.error('--live is required; this performs external reads')
    if not 1 <= args.repeat <= 5: parser.error('repeat must be 1..5')
    policy = model_network.load()
    results = []
    # Keep all capability/cooldown files isolated; use the same network policy.
    with tempfile.TemporaryDirectory() as folder:
        original = os.environ.get('DESK_COMPANION_DATA_DIR')
        try:
            os.environ['DESK_COMPANION_DATA_DIR'] = folder
            Path(folder, 'model-network.json').write_text(json.dumps(policy), encoding='utf-8')
            for repeat in range(args.repeat):
                for category, query, domains in CASES:
                    result = search_service.probe(query)
                    result.update(case_type=category, query=query, repeat=repeat + 1,
                        expected_domain_candidate=any(any(domain in row['link'] for domain in domains) for row in result.get('results', [])))
                    results.append(result)
                    print(category, repeat + 1, result.get('provider'), result['ok'], result.get('elapsed_ms'), flush=True)
        finally:
            if original is None: os.environ.pop('DESK_COMPANION_DATA_DIR', None)
            else: os.environ['DESK_COMPANION_DATA_DIR'] = original
    good = [row for row in results if row['ok']]
    times = sorted(row['elapsed_ms'] for row in good)
    report = dict(total=len(results), usable_search=len(good), transport_available=sum(any(a['ok'] or a.get('category')=='empty' for a in row.get('attempts',[])) for row in results), expected_domain_candidates=sum(row['expected_domain_candidate'] for row in good),
        median_ms=statistics.median(times) if times else None, p95_ms=times[min(len(times)-1, int(len(times)*.95))] if times else None,
        quality_note='Expected-domain candidate is a retrieval signal, not proof of relevance, truth or publication date; manually inspect snippets and original pages.', cases=results)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False), flush=True)
    return 0 if len(good)==len(results) else 1


if __name__ == '__main__': sys.exit(main())
