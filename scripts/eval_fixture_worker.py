"""Explicit evaluation-only external-service fixture around the production worker."""
import hashlib
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desk_companion.agent_eval_tools import FIXTURE_BODY,FIXTURE_URL
from desk_companion.task_worker import main


def fetch_page(url, *, on_body=None):
    if url!=FIXTURE_URL:raise ValueError('fixture permits only the fixed synthetic public source')
    value=dict(kind='page',level='body',url=url,requested_url=url,title='合成来源（模拟服务）',
        text=FIXTURE_BODY,characters=len(FIXTURE_BODY),sha256=hashlib.sha256(FIXTURE_BODY.encode()).hexdigest(),
        fetched_at='2040-01-01T00:00:00+00:00',extraction='fixture',links=[],truncated=False)
    if on_body:on_body(value)
    return value


if __name__=='__main__':
    # Only fixture-mode TaskManager uses this entry. Live uses its normal command.
    # PageReader still creates/loads scoped snapshots; no model loop is replaced.
    with patch('desk_companion.web_snapshots.fetch_page',fetch_page):
        main()
