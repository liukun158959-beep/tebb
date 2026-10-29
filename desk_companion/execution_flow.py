"""Execution readiness, phase accounting and public chain projection."""
from __future__ import annotations

import time
import re

ACTIVE = {'pending', 'queued', 'running', 'waiting', 'cancelling'}
OPEN = ACTIVE | {'needs_input'}


def explicit_search(text):
    """Honor a literal search command; ordinary explanations still use routing."""
    return bool(re.match(r'(?i)^\s*(?:(?:请|麻烦|帮我|给我|可以|能否|你能|帮忙)\s*)*(?:(?:上网|联网)\s*)?(?:搜索|查找|搜一下|搜下|search\b|look up\b)',text))


def claims_handoff(text):
    """A routing answer is never an execution receipt. Guard explicit promises.

    This is a conservative invalid-output check, not an intent classifier. The
    model still chooses answer/task/clarify, and only the scheduler starts work.
    """
    prose=re.sub(r'```[\s\S]*?```|`[^`]*`','',text)
    return bool(re.search(r'(?:已(?:经)?|正在|即将).{0,32}(?:提交|交给|委派|派发|启动|安排|转交).{0,24}(?:后台|协作|伙伴|助手|任务)',prose)
                or re.search(r'(?i)\bI\s+(?:have\s+|already\s+|am\s+)?(?:submitted|delegated|started|queued|dispatching)\b.{0,60}\b(?:background|agent|task|worker)\b',prose))


def phase_calls(run):
    return run.get('calls', 0) - run.get('phase_start_calls', 0)


def advance(run, kind, *, state='pending', status=''):
    now = time.time()
    run.setdefault('phases', []).append(dict(kind=run['kind'], calls=phase_calls(run),
        started=run.get('phase_started', run.get('created', now)), ended=now))
    run.update(kind=kind, state=state, phase_start_calls=run.get('calls', 0), phase_started=now,
               attempt=run.get('attempt', 0) + 1, cursor=0, task='', answer='', error='', status=status)
    run.pop('public_answer', None)
    run.pop('artifact', None)
    run['events'].append(status or '进入下一阶段')


def chain_view(run, runs):
    """Old graphs remain intact; never infer overall success from one node."""
    identifiers = {run['id']}
    while True:
        more = identifiers | {r['id'] for r in runs if r.get('parent') in identifiers}
        if more == identifiers:
            break
        identifiers = more
    chain = [r for r in runs if r['id'] in identifiers]
    unfinished = [r for r in chain if r['state'] in ACTIVE]
    failed = next((r for r in chain if r['state'] in {'failed', 'timed_out', 'interrupted'}), None)
    awaiting = next((r for r in chain if r['state'] == 'needs_input'), None)
    state = (failed['state'] if failed else 'needs_input' if awaiting else 'running' if unfinished
             else 'cancelled' if any(r['state'] == 'cancelled' for r in chain) else run['state'])
    final = run if run['state']=='succeeded' and run.get('artifact') else next((r for r in reversed(chain) if r.get('stage') == 'deliver' and r['state'] == 'succeeded'), run)
    elapsed = None if unfinished or awaiting or any(not r.get('finished') for r in chain) else int(
        max(r['finished'] for r in chain) * 1000 - run.get('created',time.time()) * 1000)
    return dict(state=state, calls=sum(r.get('calls', 0) for r in chain),
                tool_calls=sum(r.get('tool_calls', 0) for r in chain),
                elapsed_ms=elapsed,
                error=(failed or awaiting or run).get('error', ''),
                answer=final.get('answer', ''), children=[r['id'] for r in chain if r['id'] != run['id']])
