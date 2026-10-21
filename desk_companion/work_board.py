"""User-owned work items; execution state and scheduling remain in their domains."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading

from . import work_items
from .work_items import BoardRuleError


class WorkBoardService:
    def __init__(self, path=None):
        from .collaboration import workspace_path
        self.path = Path(path or workspace_path())
        self._ready = False
        self._init_lock = threading.Lock()

    def _ensure(self):
        with self._init_lock:
            if self._ready:
                return
            from .collaboration import initialize_workspace
            initialize_workspace(self.path)
            with closing(sqlite3.connect(self.path)) as db:
                db.execute('CREATE TABLE IF NOT EXISTS work_receipts '
                           '(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL)')
                db.commit()
            self._ready = True

    @staticmethod
    def _request_id(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', value):
            raise BoardRuleError('缺少有效的事项操作编号，请刷新后重试。')
        return value

    def load(self, request_id=''):
        try:
            if request_id:
                self._request_id(request_id)
            self._ensure()
            from .collaboration import transaction
            with transaction(self.path) as (state, db):
                result = work_items.public_page(state)
                receipt = db.execute('SELECT result FROM work_receipts WHERE id=?', (request_id,)).fetchone() if request_id else None
                result['receipt'] = json.loads(receipt[0]) if receipt else None
            return result
        except Exception as exc:
            return dict(ok=False, error=f'事项读取失败：{exc}。请重新读取核对。', stage='read')

    def change(self, action):
        request_id = action.get('request_id', '') if isinstance(action, dict) else ''
        attempted, receipt = False, None
        try:
            self._request_id(request_id)
            if not isinstance(action.get('type'), str):
                raise BoardRuleError('事项操作无效。')
            fingerprint = hashlib.sha256(json.dumps(action, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            self._ensure()
            from .collaboration import transaction
            with transaction(self.path) as (state, db):
                row = db.execute('SELECT fingerprint,result FROM work_receipts WHERE id=?', (request_id,)).fetchone()
                if row:
                    if row[0] != fingerprint:
                        raise BoardRuleError('此操作编号已被其他事项操作使用。')
                    receipt = json.loads(row[1])
                else:
                    item = work_items.apply_change(state, action)
                    receipt = dict(request_id=request_id, item_id=item['id'], version=item['version'], operation=action['type'])
                    db.execute('INSERT INTO work_receipts VALUES(?,?,?)', (request_id, fingerprint, json.dumps(receipt)))
                attempted = True
        except BoardRuleError as exc:
            return dict(ok=False, error=str(exc), conflict=exc.conflict,
                        request_id=request_id, change_state='not_changed', stage='validate')
        except Exception as exc:
            return dict(ok=False, request_id=request_id, receipt=receipt,
                        change_state='unconfirmed' if attempted else 'not_changed', stage='save' if attempted else 'read',
                        error=(f'事项保存结果未确认：{exc}。请重新读取并核对操作编号，不要自动重发。' if attempted
                               else f'事项操作失败：{exc}。请重新读取。'))
        page = self.load(request_id)
        if not page['ok']:
            return dict(ok=False, request_id=request_id, receipt=receipt, change_state='committed', stage='refresh',
                        error='事项操作已保存，但页面读取失败。请重新读取核对，不要重复提交。')
        return page
