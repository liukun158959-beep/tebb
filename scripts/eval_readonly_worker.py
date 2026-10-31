"""Explicit frozen-document boundary; production worker and model remain intact."""
import hashlib
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desk_companion.paths import data_root
from desk_companion.agent_eval_research import load_corpus
from desk_companion.task_worker import main

scope,corpus=load_corpus(data_root()/'memory/eval_research_corpus')

def frozen_page(url,*,on_body=None):
    if url not in corpus:raise ValueError('comparison permits only the two frozen source URLs')
    source=corpus[url];body=source['text']
    page=dict(kind='page',level='body',url=url,requested_url=url,title='Frozen source snapshot',text=body,characters=len(body),
        sha256=hashlib.sha256(body.encode()).hexdigest(),fetched_at=source['fetched_at'],extraction=scope,links=[],truncated=False)
    if on_body:on_body(page)
    return page

if __name__=='__main__':
    with patch('desk_companion.web_snapshots.fetch_page',frozen_page):main()
