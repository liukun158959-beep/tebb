"""Local metadata observations, separate from quota admission and debug payloads."""
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import time

from .paths import data_root


def number(value):
    try:return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
    except OverflowError:return None


def tokens(value):
    return value if type(value) is int and 0 <= value <= 2**53-1 else None


def normalized_usage(usage):
    usage = usage if isinstance(usage, dict) else {}
    inn = tokens(usage.get('prompt_tokens', usage.get('input_tokens')))
    out = tokens(usage.get('completion_tokens', usage.get('output_tokens')))
    details = usage.get('prompt_tokens_details') or usage.get('input_tokens_details') or {}
    cached = tokens(details.get('cached_tokens')) if isinstance(details, dict) else None
    completion = usage.get('completion_tokens_details') or usage.get('output_tokens_details') or {}
    reasoning = tokens(completion.get('reasoning_tokens')) if isinstance(completion, dict) else None
    invalid = cached is not None and inn is not None and cached > inn or reasoning is not None and out is not None and reasoning > out
    if cached is not None and inn is not None and cached > inn: cached = None
    if reasoning is not None and out is not None and reasoning > out: reasoning = None
    return dict(input_tokens=inn, output_tokens=out, cached_tokens=cached, reasoning_tokens=reasoning,
                total_tokens=inn+out if inn is not None and out is not None else None,
                usage_quality='invalid_details' if invalid else 'complete' if inn is not None and out is not None else 'partial' if inn is not None or out is not None else 'unknown')


def estimate(usage, prices):
    if not isinstance(prices, dict): return None
    inn, out = usage.get('input_tokens'), usage.get('output_tokens')
    ip, op = number(prices.get('input_cny_per_mtok')), number(prices.get('output_cny_per_mtok'))
    if inn is None or out is None or ip is None or op is None: return None
    amount=number((inn*ip+out*op)/1_000_000)
    return round(amount,8)if amount is not None else None


def read_rows(path, sql, parameters=()):
    """Owner-provided metadata SELECT only; never create a database on a read."""
    path = Path(path)
    if not path.is_file(): return []
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(sql, parameters).fetchmany(100001)
        if len(rows)>100000: raise RuntimeError('记录超过单次统计范围，请缩小日期范围。')
        return [dict(row) for row in rows]


class UsageStore:
    def __init__(self, path=None):
        self.path = Path(path or data_root()/'memory/usage-observations.sqlite3')

    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, kind TEXT NOT NULL, '
                   'started REAL NOT NULL, updated REAL NOT NULL, root TEXT NOT NULL, task TEXT NOT NULL, data TEXT NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS records_time ON records(started,id)')
        db.execute('CREATE INDEX IF NOT EXISTS records_root ON records(root)')
        return db

    def begin(self, ident, kind, metadata, *, now=None):
        if kind not in {'model', 'tool'} or not isinstance(ident, str) or not ident:
            raise ValueError('无效用量身份。')
        if set(metadata)-{'root','task','model_id','model','model_key','route','project_id','channel','source','tool','parent_call','price_snapshot','purpose'}:
            raise ValueError('用量记录只允许元数据。')
        now = time.time() if now is None else now
        if number(now) is None: raise ValueError('无效用量时间。')
        value = {**metadata, 'version':1, 'id':ident, 'kind':kind, 'started':now, 'updated':now, 'state':'running'}
        with closing(self._db()) as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT kind,root,task FROM records WHERE id=?', (ident,)).fetchone()
            if old:
                if (old['kind'],old['root'],old['task']) != (kind,metadata.get('root',''),metadata.get('task','')):
                    raise ValueError('用量身份已绑定其它执行。')
                return False
            db.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?)', (ident,kind,now,now,metadata.get('root',''),metadata.get('task',''),json.dumps(value,ensure_ascii=False,allow_nan=False)))
            db.commit()
        return True

    def update(self, ident, **changes):
        allowed = {'state','ended','error_kind','finish_reason','usage','cost_cny','network_attempts','result_kind'}
        if set(changes)-allowed: raise ValueError('不允许修改用量身份或归属。')
        if 'state'in changes and changes['state']not in{'running','returned','failed','cancelled','unknown','rejected','cached','replayed'}:raise ValueError('用量状态无效。')
        if 'usage'in changes and(not isinstance(changes['usage'],dict)or set(changes['usage'])-{'input_tokens','output_tokens','cached_tokens','reasoning_tokens','total_tokens','usage_quality'}):raise ValueError('用量只允许计数元数据。')
        with closing(self._db()) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM records WHERE id=?', (ident,)).fetchone()
            if row is None: raise ValueError('原用量记录不存在。')
            value = json.loads(row['data']); value.update(changes, updated=time.time())
            db.execute('UPDATE records SET updated=?,data=? WHERE id=?', (value['updated'],json.dumps(value,ensure_ascii=False,allow_nan=False),ident))
            db.commit()

    def rows(self, since=0, until=None):
        until = time.time() if until is None else until
        rows = read_rows(self.path, 'SELECT data FROM records WHERE started>=? AND started<? ORDER BY started DESC,id DESC', (since,until))
        return [json.loads(row['data']) for row in rows]

    def model_ids(self,owners):
        result=set();owners=list(owners)
        for offset in range(0,len(owners),400):
            group=owners[offset:offset+400]
            result.update(row['id']for row in read_rows(self.path,"SELECT id FROM records WHERE kind='model'AND root IN("+','.join('?'for _ in group)+')',group))
        return result

    def first_observation(self):
        rows=read_rows(self.path,'SELECT MIN(started) AS started FROM records')
        return rows[0]['started']if rows else None
