"""Local calendar facts and explicit publishing. Remote caches never own local identity."""
from contextlib import ExitStack
import copy
from datetime import datetime
import hashlib
import json
import logging

from . import schedule_domain as domain, work_items
from .board_data import calendar_operation
from .calendar_adapter import CalendarError, creation_fields, identifier
from .calendar_time import TZ, day_range

LOCAL = 'tebb-local'
ACTIVE = {'prepared', 'calling', 'remote_created', 'unconfirmed'}
log = logging.getLogger(__name__)


def fingerprint(action):
    return hashlib.sha256(json.dumps(action, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class LocalCalendarService:
    def __init__(self, scheduling):
        self.scheduling = scheduling
        self.board = scheduling.board

    @staticmethod
    def records(state):
        return state.setdefault('local_calendar_events', {})

    @staticmethod
    def operations(state):
        return state.setdefault('local_calendar_operations', {})

    @staticmethod
    def public(operation):
        if operation is None:
            return None
        return {k: copy.deepcopy(v) for k, v in operation.items() if k not in {'fingerprint', 'owner_id'}}

    @staticmethod
    def public_batch(batch):
        result = copy.deepcopy(batch)
        result.get('target', {}).pop('owner_id', None)
        return result

    def require(self, state, uid):
        identifier(uid)
        row = self.records(state).get(uid)
        if not row:
            raise ValueError('本地日程不存在，请重新读取。')
        return row

    def idle(self, state, row):
        if any(o.get('type')=='delete' and o.get('local_id')==row['id'] and o['state'] in ACTIVE for o in self.operations(state).values()):
            raise ValueError('此日程删除结果待核对。')
        op = self.operations(state).get(row.get('sync_operation'))
        if op and op['state'] in ACTIVE:
            raise ValueError('同步执行中或结果未确认，请先按原编号核对。')
        if row.get('remote'):
            domain.require_time_idle(state, **row['remote'])

    def view(self, state, row, remote_error=''):
        remote = row.get('remote_snapshot') or {}
        synced = bool(row.get('remote'))
        now = row.get('updated_at') or row['created_at']
        event = dict(calendar_id=LOCAL, event_id=row['id'], local_id=row['id'], local_version=row['version'],
            summary=row['summary'], start=row['start'], end=row['end'], status=remote.get('status', 'confirmed'),
            eligible=remote.get('eligible', False) if synced else True, reason=remote.get('reason', '') if synced else '',
            verification=remote.get('verification', 'confirmed') if synced else 'confirmed',
            error=remote_error or remote.get('error', ''), checked_at=remote.get('checked_at', now) if synced else now,
            editable=remote.get('editable', False) if synced else True,
            edit_reason=remote.get('edit_reason', '核对飞书编辑资格') if synced else '',
            edit_checked_at=remote.get('edit_checked_at', '') if synced else now,
            sync_state='synced' if synced else row.get('sync_state', 'local'), remote=copy.deepcopy(row.get('remote')),
            sync_operation=row.get('sync_operation', ''))
        try:
            self.idle(state, row)
            link = next((l for l in state.get('schedule_links', []) if domain.event_key(l['event']) == LOCAL+'/'+row['id']), None)
            if link and work_items.require_item(state, link['item_id'])['archived']:
                raise ValueError('关联事项已归档，请先恢复事项。')
            if datetime.fromisoformat(row['start']).date() != datetime.fromisoformat(row['end']).date():
                raise ValueError('跨日日程本批只读。')
        except ValueError as exc:
            event.update(editable=False, edit_reason=str(exc))
        if synced and remote_error:
            event.update(editable=False, verification='read_failed', edit_reason=remote_error)
        return event

    def observe(self, row, event):
        old = row.get('remote_snapshot', {})
        if old.get('checked_at', '') > event.get('checked_at', ''):
            return
        if event.get('verification') == 'confirmed':
            if any(row.get(k) != event.get(k) for k in ('summary', 'start', 'end')):
                row.update({k: event[k] for k in ('summary', 'start', 'end')})
                row['version'] += 1
        row['remote_snapshot'] = copy.deepcopy(event)

    def project(self, page, state, week=False):
        """Merge local facts and remote observations; one visual identity after publishing."""
        rows = self.records(state)
        mapping = {domain.event_key(r['remote']): r for r in rows.values() if r.get('remote')}
        native = []
        for event in page['events']:
            if event['calendar_id'] == LOCAL:
                continue  # These are derived cache entries, never a second local fact.
            row = mapping.get(domain.event_key(event))
            if row:
                self.observe(row, event)
            else:
                native.append(event)
        a, b = domain.week_range(page['day']) if week else day_range(page['day'])
        for row in rows.values():
            event = self.view(state, row, page.get('events_error', '') if not page['events_valid'] else '')
            if row.get('remote') and page.get('calendar') and page['calendar']['id'] != row['remote']['calendar_id']:
                event.update(editable=False, verification='unavailable', edit_reason='当前主日历与原同步目标不同，请核对飞书身份。')
            if datetime.fromisoformat(event['start']) < b and datetime.fromisoformat(event['end']) > a:
                native.append(event)
            domain.update_event(state, event)
        page['events'] = sorted(native, key=lambda e: (e['start'], e['event_id']))
        page['links'] = copy.deepcopy(state.get('schedule_links', []))
        return page

    def refresh_missing(self, calendar, events, day, week=False):
        """A disappeared range entry may have moved; absence is never evidence of deletion."""
        a, b = domain.week_range(day) if week else day_range(day)
        known = {domain.event_key(e) for e in events}
        with self.board.workspace_transaction() as (state, _):
            targets = [copy.deepcopy(r) for r in self.records(state).values() if r.get('remote')
                       and r['remote']['calendar_id'] == calendar['id'] and domain.event_key(r['remote']) not in known
                       and datetime.fromisoformat(r['start']) < b and datetime.fromisoformat(r['end']) > a]
        from concurrent.futures import ThreadPoolExecutor
        def observe(row):
            try:
                return self.scheduling.calendar.get(**row['remote'])
            except Exception as exc:
                old = row.get('remote_snapshot', {})
                return dict(old, **row['remote'], verification=getattr(exc, 'status', 'read_failed'),
                            error=str(exc), checked_at=datetime.now(TZ).isoformat(), editable=False, edit_reason=str(exc))
        with ThreadPoolExecutor(max_workers=2) as pool:
            return events+list(pool.map(observe, targets))

    def load(self, request_id=''):
        try:
            if request_id:
                self.board.validate_request_id(request_id)
            with self.board.workspace_transaction() as (state, _):
                ops = self.operations(state)
                operation = ops.get(request_id)
                if not operation and request_id in state.get('calendar_sync_batches', {}):
                    operation = dict(id=request_id, type='prepare_sync', state='saved', stage='committed', error='')
                return dict(ok=True, events=[self.view(state, r) for r in self.records(state).values()],
                    operations=[self.public(o) for o in ops.values() if o['state'] in ACTIVE],
                    operation=self.public(operation),
                    batches=[self.public_batch(b) for b in state.get('calendar_sync_batches', {}).values()])
        except Exception as exc:
            return dict(ok=False, stage='read', error=f'本地日程读取失败：{exc}')

    def query(self, day):
        """Read saved local facts only; never refresh or authorize remote changes."""
        scope = dict(scope='local_snapshot', remote_refreshed=False)
        try:
            start, end = day_range(day)
            page = self.load()
            if not page['ok']:
                return {**page, **scope}
            events = [event for event in page['events']
                      if datetime.fromisoformat(event['start']) < end
                      and datetime.fromisoformat(event['end']) > start]
            events.sort(key=lambda event: (datetime.fromisoformat(event['start']), event['event_id']))
            fields = ('event_id', 'summary', 'start', 'end', 'sync_state', 'verification', 'checked_at')
            return dict(ok=True, day=start.date().isoformat(), **scope, total=len(events),
                        truncated=len(events)>100,
                        events=[{key:event[key] for key in fields} for event in events[:100]])
        except (ValueError, TypeError, KeyError) as exc:
            return dict(ok=False, stage='read', **scope, error=f'本地日程查询未完成：{exc}')

    def result(self, operation, event=None):
        successful = operation['state'] in {'saved', 'synced', 'resolved_absent', 'retimed', 'observed', 'deleted'}
        return dict(ok=successful, operation=self.public(operation), event=event,
                    request_id=operation['id'], stage=operation['stage'],
                    change_state='committed' if successful else 'unconfirmed' if operation['state'] in ACTIVE else 'not_changed',
                    error=operation.get('error', ''))

    def _save_operation(self, operation):
        with self.board.workspace_transaction() as (state, _):
            old = self.operations(state).get(operation['id'])
            if old and old['state'] in {'synced', 'resolved_absent'}:
                return
            self.operations(state)[operation['id']] = copy.deepcopy(operation)
            row = self.records(state).get(operation.get('local_id'))
            if row and operation['type'] == 'sync':
                row['sync_operation'] = operation['id']
                row['sync_state'] = 'unconfirmed' if operation['state'] in ACTIVE else 'local'
        log.info('calendar operation=%s local=%s type=%s stage=%s state=%s', operation['id'],
                 operation.get('local_id', ''), operation['type'], operation['stage'], operation['state'])

    def _attach(self, state, row, item_id):
        old = next((l for l in state.get('schedule_links', []) if domain.event_key(l['event']) == LOCAL+'/'+row['id']), None)
        if old and old['item_id'] != item_id:
            domain.detach(state, old['item_id'], LOCAL, row['id'])
            work_items.touch_item(work_items.require_item(state, old['item_id']))
        if item_id:
            item = work_items.require_item(state, item_id)
            if item['archived']:
                raise ValueError('请先恢复归档事项。')
            domain.attach(state, item_id, self.view(state, row))
            work_items.touch_item(item)

    def save(self, action):
        attempted = False
        try:
            rid = self.board.validate_request_id(action.get('request_id'))
            uid = identifier(action.get('local_id') or rid)
            item_id = action.get('item_id', '')
            fields = creation_fields(action.get('summary'), action.get('start'), action.get('end'), allow_past=action.get('type')=='update')
            with self.scheduling.hold_event(LOCAL, uid), ExitStack() as leases:
                with self.board.workspace_transaction() as (state, _):
                    old_link = next((l['item_id'] for l in state.get('schedule_links', [])
                                     if domain.event_key(l['event']) == LOCAL+'/'+uid), '')
                for item in sorted({i for i in (item_id, old_link) if i}):
                    leases.enter_context(self.board.hold_item(item))
                with self.board.workspace_transaction() as (state, _):
                    existing = self.operations(state).get(rid)
                    if existing:
                        if existing['fingerprint'] != fingerprint(action):
                            raise ValueError('操作编号已用于其他请求。')
                        return self.result(existing, self.view(state, self.require(state, existing['local_id'])))
                    row = self.records(state).get(uid)
                    if row:
                        if action.get('type') != 'update' or action.get('version') != row['version']:
                            raise ValueError('日程已更新，请重新读取后编辑。')
                        self.idle(state, row)
                        if old_link and work_items.require_item(state, old_link)['archived']:
                            raise ValueError('请先恢复关联事项。')
                        if not row.get('remote') and not self.view(state,row)['editable']:raise ValueError(self.view(state,row)['edit_reason'])
                        if row.get('remote') and any(fields[k] != row[k] for k in fields):
                            raise ValueError('已同步日程请使用改期入口，标题暂不修改。')
                        row.update(fields, version=row['version']+1, updated_at=datetime.now(TZ).isoformat())
                    else:
                        if action.get('type') != 'create':
                            raise ValueError('本地日程不存在。')
                        row = dict(id=uid, version=1, sync_state='local', created_at=datetime.now(TZ).isoformat(), **fields)
                        self.records(state)[uid] = row
                    self._attach(state, row, item_id)
                    event = self.view(state, row)
                    domain.cache_event(state, event)
                    op = dict(id=rid, fingerprint=fingerprint(action), local_id=uid, type='save',
                              state='saved', stage='committed', error='', event=event)
                    self.operations(state)[rid] = op
                    attempted = True
                return self.result(op, event)
        except Exception as exc:
            return dict(ok=False, stage='save' if attempted else 'validate', request_id=action.get('request_id'),
                change_state='unconfirmed' if attempted else 'not_changed',
                error=('本地保存结果未确认，请按原编号读取核对。' if attempted else '')+str(exc))

    def inspect(self, uid):
        try:
            with self.board.workspace_transaction() as (state, _):
                row = copy.deepcopy(self.require(state, uid))
            result = None
            if row.get('remote'):
                result = self.scheduling.inspect_event(**row['remote'])
            with self.board.workspace_transaction() as (state, _):
                row = self.require(state, uid)
                if result and result.get('event'):
                    self.observe(row, result['event'])
                event = self.view(state, row)
                domain.update_event(state, event)
                return dict(ok=result['ok'] if result else True, event=event, error=result.get('error', '') if result else '')
        except Exception as exc:
            return dict(ok=False, stage='inspect', error=str(exc))

    def link(self, action):
        attempted = False
        try:
            rid = self.board.validate_request_id(action.get('request_id'))
            uid, item_id = identifier(action.get('event_id')), identifier(action.get('item_id'))
            with self.scheduling.hold_event(LOCAL, uid), self.board.hold_item(item_id):
                with self.board.workspace_transaction() as (state, _):
                    operations = state.setdefault('schedule_operations', {})
                    op = operations.get(rid)
                    if op:
                        if op['fingerprint'] != fingerprint(action):
                            raise ValueError('操作编号已用于其他请求。')
                    else:
                        row = self.require(state, uid)
                        self.idle(state, row)
                        item = work_items.require_item(state, item_id)
                        work_items.check_version(item, action.get('version'))
                        if item['archived']:
                            raise ValueError('请先恢复关联事项。')
                        if action['type'] == 'link':
                            domain.attach(state, item_id, self.view(state, row))
                        else:
                            domain.detach(state, item_id, LOCAL, uid)
                        row['version'] += 1
                        work_items.touch_item(item)
                        op = dict(id=rid, fingerprint=fingerprint(action), type=action['type'], item_id=item_id,
                                  state='linked' if action['type'] == 'link' else 'unlinked', stage='committed', error='')
                        operations[rid] = op
                    attempted = True
                return self.scheduling._result(op, action['day'])
        except Exception as exc:
            return dict(ok=False, stage='local_link', change_state='unconfirmed' if attempted else 'not_changed', error=str(exc))

    def retime(self, action):
        uid = action.get('event_id')
        attempted = False
        remote_result = None
        try:
            rid = self.board.validate_request_id(action.get('request_id'))
            with self.scheduling.hold_event(LOCAL, uid):
                with self.board.workspace_transaction() as (state, _):
                    row = copy.deepcopy(self.require(state, uid))
                    link = next((l for l in state.get('schedule_links', []) if domain.event_key(l['event']) == LOCAL+'/'+uid), None)
                    item_id = link['item_id'] if link else ''
                if row.get('remote'):
                    mapped = dict(action, **row['remote'])
                    with (self.board.hold_item(item_id) if item_id else ExitStack()):
                        with self.board.workspace_transaction() as (state, _):
                            if item_id and work_items.require_item(state, item_id)['archived']:
                                raise ValueError('请先恢复关联事项。')
                        result = remote_result = self.scheduling.change(mapped)
                        remote = (result.get('operation') or {}).get('event')
                        if remote:
                            with self.board.workspace_transaction() as (state, _):
                                current = self.require(state, uid)
                                self.observe(current, remote)
                                result['operation']['event'] = self.view(state, current)
                                domain.cache_event(state, result['operation']['event'])
                                if item_id and result['operation']['state'] in {'retimed', 'observed', 'deleted'}:
                                    work_items.touch_item(work_items.require_item(state, item_id))
                        return result
                if action['type'] == 'reconcile_time':
                    original_id = self.board.validate_request_id(action.get('operation_id'))
                    if original_id == rid or action.get('confirmed') is not True:
                        raise ValueError('请用新编号明确核对当前本地时间。')
                    with self.board.workspace_transaction() as (state, _):
                        current = self.require(state, uid)
                        if not domain.same_time(current, action.get('original_start'), action.get('original_end')):
                            raise ValueError('本地时间又有变化，请重新读取。')
                        operations = self.operations(state)
                        old = operations.get(original_id)
                        if old and old.get('local_id') != uid:
                            raise ValueError('原编号属于其他日程。')
                        if not old:
                            operations[original_id] = dict(id=original_id, type='retime', fingerprint='', local_id=uid,
                                state='observed', stage='committed', error='已登记当前本地值，原请求不再执行。')
                        op = dict(id=rid, local_id=uid, type='reconcile_time', state='observed', stage='committed',
                                  event=self.view(state, current), error='')
                        operations[rid] = op
                    result = self.scheduling.load_week(action['day'])
                    result.update(self.result(op, op['event']))
                    return result
                if datetime.fromisoformat(row['start']).date()!=datetime.fromisoformat(row['end']).date():raise ValueError('跨日日程本批只读。')
                fields = domain.time_fields(action)
                with (self.board.hold_item(item_id) if item_id else ExitStack()):
                    with self.board.workspace_transaction() as (state, _):
                        row = self.require(state, uid)
                        op = self.operations(state).get(rid)
                        if op:
                            if op['fingerprint'] != fingerprint(action):
                                raise ValueError('编号已用于其他请求。')
                        else:
                            self.idle(state, row)
                            if item_id and work_items.require_item(state, item_id)['archived']:
                                raise ValueError('请先恢复关联事项。')
                            if not domain.same_time(row, fields['original_start'], fields['original_end']):
                                raise ValueError('本地时间已变化，请重新调整。')
                            if action.get('summary') is not None:
                                if action.get('original_summary')!=row['summary']:raise ValueError('本地标题已变化，请重新读取后修改。')
                                row['summary']=creation_fields(action['summary'],fields['start'],fields['end'],allow_past=True)['summary']
                            row.update(start=fields['start'], end=fields['end'], version=row['version']+1,
                                       updated_at=datetime.now(TZ).isoformat())
                            event = self.view(state, row)
                            domain.cache_event(state, event)
                            op = dict(id=rid, fingerprint=fingerprint(action), local_id=uid, type='retime',
                                      state='retimed', stage='committed', error='', event=event)
                            self.operations(state)[rid] = op
                        attempted = True
                result = self.scheduling.load_week(action['day'])
                result.update(self.result(op, op['event']))
                return result
        except Exception as exc:
            if remote_result:
                op = remote_result.get('operation') or {}
                if op.get('event'):
                    op['event'] = dict(op['event'], calendar_id=LOCAL, event_id=uid, local_id=uid,
                                       remote=row.get('remote'), local_version=row.get('version'), sync_state='synced')
                return dict(remote_result, ok=False, stage='local_register',
                    change_state='committed' if op.get('state') in {'retimed', 'observed', 'unchanged'} else 'unconfirmed',
                    error='飞书改期已有回执，本地刷新未完整确认，请按原编号重新读取。'+str(exc))
            return dict(ok=False, change_state='unconfirmed' if attempted else 'not_changed', stage='local_time',
                        request_id=action.get('request_id'), error=str(exc))

    def sync(self, action):
        op, remote_started, attempted = None, False, False
        try:
            rid = self.board.validate_request_id(action.get('request_id'))
            uid = identifier(action.get('local_id'))
            with self.scheduling.hold_event(LOCAL, uid), ExitStack() as leases:
                with self.board.workspace_transaction() as (state, _):
                    row = copy.deepcopy(self.require(state, uid))
                    old = copy.deepcopy(self.operations(state).get(rid))
                    item_id = next((l['item_id'] for l in state.get('schedule_links', []) if domain.event_key(l['event']) == LOCAL+'/'+uid), '')
                if old:
                    if old['fingerprint'] != fingerprint(action):
                        raise ValueError('同步编号已用于其他请求。')
                    return self.result(old)
                if item_id:
                    leases.enter_context(self.board.hold_item(item_id))
                with self.board.workspace_transaction() as (state, _):
                    current = self.require(state, uid)
                    self.idle(state, current)
                    if current.get('remote'):
                        raise ValueError('此日程已同步，不会重复创建。')
                    if action.get('version') != current['version']:
                        raise ValueError('日程已变化，请重新预览再同步。')
                    if item_id and work_items.require_item(state, item_id)['archived']:
                        raise ValueError('请先恢复关联事项。')
                fields = creation_fields(row['summary'], row['start'], row['end'])
                with calendar_operation():
                    calendar = self.scheduling.calendar.primary()
                    if not calendar.get('writable'):
                        raise ValueError('当前飞书主日历不可写。')
                    if action.get('calendar_id') and calendar['id'] != action['calendar_id']:
                        raise ValueError('主日历已变化，请重新预览同步目标。')
                    if action.get('owner_id') and calendar.get('owner_id') != action['owner_id']:
                        raise ValueError('飞书身份已变化，请先核对原同步清单。')
                    op = dict(id=rid, fingerprint=fingerprint(action), type='sync', local_id=uid,
                              state='prepared', stage='prepare', calendar_id=calendar['id'],
                              owner_id=calendar.get('owner_id', ''), fields=fields, error='')
                    attempted = True
                    self._save_operation(op)
                    op.update(state='calling', stage='remote_create')
                    self._save_operation(op)
                    identity = self.scheduling.calendar.primary()
                    if (identity['id'], identity.get('owner_id')) != (calendar['id'], calendar.get('owner_id')):
                        raise ValueError('飞书身份在准备期间变化，尚未发送创建，请核对。')
                    remote_started = True
                    event = self.scheduling.calendar.create(calendar['id'], rid, fields)
                    if event.get('calendar_id') != calendar['id'] or not event.get('event_id'):
                        raise ValueError('飞书创建回执身份不完整，请核对。')
                    op.update(state='remote_created', stage='register', event=event)
                    self._save_operation(op)
                    with self.board.workspace_transaction() as (state, _):
                        current = self.require(state, uid)
                        self._bind(state, current, event)
                        op.update(state='synced', stage='committed')
                        self.operations(state)[rid] = copy.deepcopy(op)
                        result_event = self.view(state, current)
                    log.info('calendar operation=%s local=%s stage=committed state=synced', rid, uid)
                    return self.result(op, result_event)
        except Exception as exc:
            if op:
                try:
                    with self.board.workspace_transaction() as (state, _):
                        saved = self.operations(state).get(op['id'])
                        if saved and saved['state'] == 'synced':
                            return self.result(saved, self.view(state, self.require(state, saved['local_id'])))
                except Exception:
                    pass
                # A confirmed prepared/calling receipt followed by a preflight failure is known not sent.
                op.update(state='unconfirmed' if remote_started or attempted and op['stage']=='prepare' else 'not_started', error=str(exc))
                try:
                    self._save_operation(op)
                except Exception:
                    pass
                return self.result(op)
            return dict(ok=False, change_state='not_changed', stage='sync_admission', error=str(exc))

    def _bind(self, state, row, event):
        for other in self.records(state).values():
            if other['id'] != row['id'] and other.get('remote') and domain.event_key(other['remote']) == domain.event_key(event):
                raise ValueError('此飞书日程已关联另一本地日程。')
        local_link = next((l for l in state.get('schedule_links', []) if domain.event_key(l['event']) == LOCAL+'/'+row['id']), None)
        remote_link = next((l for l in state.get('schedule_links', []) if domain.event_key(l['event']) == domain.event_key(event)), None)
        if remote_link:
            if local_link and local_link['item_id'] != remote_link['item_id']:
                raise ValueError('飞书日程已属于另一事项，请先明确解除。')
            domain.detach(state, remote_link['item_id'], event['calendar_id'], event['event_id'])
        row.update(remote={k: event[k] for k in ('calendar_id', 'event_id')}, sync_state='synced')
        self.observe(row, event)
        row['version'] += 1
        if remote_link and not local_link:
            domain.attach(state, remote_link['item_id'], self.view(state, row))
        domain.cache_event(state, self.view(state, row))

    def resolve(self, action):
        attempted = False
        try:
            rid = self.board.validate_request_id(action.get('request_id'))
            uid = identifier(action.get('local_id'))
            with self.scheduling.hold_event(LOCAL, uid), ExitStack() as leases:
                with self.board.workspace_transaction() as (state, _):
                    row = copy.deepcopy(self.require(state, uid))
                    original = copy.deepcopy(self.operations(state).get(action.get('operation_id')))
                    if row.get('remote'):
                        raise ValueError('此本地日程已绑定飞书，请读取已有同步结果。')
                    if original and (original.get('local_id') != uid or original['state'] not in ACTIVE):
                        raise ValueError('原同步操作已处理或不存在，请重新读取。')
                    item_id = next((l['item_id'] for l in state.get('schedule_links', []) if domain.event_key(l['event']) == LOCAL+'/'+uid), '')
                if item_id:
                    leases.enter_context(self.board.hold_item(item_id))
                calendar = self.scheduling.calendar.primary()
                if not original:
                    original_id = self.board.validate_request_id(action.get('operation_id'))
                    if original_id == rid:
                        raise ValueError('核对必须使用新的编号。')
                    original = dict(id=original_id, fingerprint='', type='sync', local_id=uid,
                        state='unconfirmed', stage='prepare', calendar_id=calendar['id'], owner_id=calendar.get('owner_id', ''),
                        error='原请求尚未登记，已封存编号等待明确核对。')
                    self._save_operation(original)
                if calendar['id'] != original.get('calendar_id') or (original.get('owner_id') and calendar.get('owner_id') != original['owner_id']):
                    raise ValueError('飞书身份或主日历已变化，请先核对原身份。')
                resolution = action.get('resolution')
                event = None
                if resolution == 'link':
                    eid = identifier(action.get('event_id'))
                    if original.get('event') and eid != original['event']['event_id']:
                        raise ValueError('原回执已有日程身份，请核对同一日程。')
                    event = self.scheduling.calendar.get(calendar['id'], eid)
                    if not event.get('eligible'):
                        raise ValueError(event.get('reason') or '不能关联此日程类型。')
                elif resolution == 'absent' and action.get('confirmed') is True:
                    if original.get('event'):
                        try:
                            known = original['event']
                            current = self.scheduling.calendar.get(known['calendar_id'], known['event_id'])
                            if current['verification'] not in {'missing', 'deleted'}:
                                raise ValueError('已知飞书日程仍存在，请关联，不可认作未创建。')
                        except CalendarError as exc:
                            if exc.status not in {'missing', 'deleted'}:
                                raise
                else:
                    raise ValueError('请明确关联已创建的日程，或确认尚未创建。')
                with self.board.workspace_transaction() as (state, _):
                    current = self.require(state, uid)
                    if rid in self.operations(state):
                        raise ValueError('核对编号已被使用，请重新读取。')
                    if event:
                        self._bind(state, current, event)
                    else:
                        current['sync_state'] = 'local'
                    old = self.operations(state)[original['id']]
                    old.update(state='synced' if event else 'resolved_absent', stage='committed', resolved_by=rid, error='')
                    op = dict(id=rid, type='resolve_sync', local_id=uid, state=old['state'], stage='committed', error='')
                    self.operations(state)[rid] = op
                    attempted = True
                    return self.result(op, self.view(state, current))
        except Exception as exc:
            return dict(ok=False, stage='reconcile', change_state='unconfirmed' if attempted else 'not_changed',
                        error=('核对结果保存未确认，请按编号重新读取。' if attempted else '')+str(exc))

    def prepare_batch(self, action):
        try:
            rid = self.board.validate_request_id(action.get('request_id'))
            manifest = action.get('events')
            if not isinstance(manifest, list) or not manifest or len(manifest) > 1000:
                raise ValueError('请选择1–1000条待同步日程。')
            calendar = self.scheduling.calendar.primary()
            if not calendar.get('writable'):
                raise ValueError('当前主日历不可写。')
            with self.board.workspace_transaction() as (state, _):
                batches = state.setdefault('calendar_sync_batches', {})
                if rid in batches:
                    if batches[rid]['manifest'] != manifest:
                        raise ValueError('同步清单编号已被使用。')
                    return dict(ok=True, batch=self.public_batch(batches[rid]))
                ids = set()
                for entry in manifest:
                    row = self.require(state, entry.get('local_id'))
                    if row['id'] in ids or row['version'] != entry.get('version') or row.get('remote'):
                        raise ValueError('清单中的日程已变化，请重新预览。')
                    self.idle(state, row)
                    ids.add(row['id'])
                batch = dict(id=rid, manifest=copy.deepcopy(manifest), created_at=datetime.now(TZ).isoformat(),
                    target=dict(calendar_id=calendar['id'], owner_id=calendar.get('owner_id', ''), summary=calendar.get('summary', '主日历')),
                    entries=[dict(local_id=e['local_id'], version=e['version'], request_id=rid+'_'+str(i), state='queued') for i, e in enumerate(manifest)])
                batches[rid] = batch
            return dict(ok=True, batch=self.public_batch(batch))
        except Exception as exc:
            return dict(ok=False, stage='batch_prepare', error=str(exc))

    def batch_step(self, batch_id, local_id):
        try:
            self.board.validate_request_id(batch_id)
            with self.scheduling.hold_event('sync-batch', batch_id):
                with self.board.workspace_transaction() as (state, _):
                    batch = state.get('calendar_sync_batches', {}).get(batch_id)
                    entry = next((e for e in (batch or {}).get('entries', []) if e['local_id'] == local_id), None)
                    if not entry:
                        raise ValueError('同步清单条目不存在。')
                    if entry['state'] != 'queued':
                        return dict(ok=True, batch=self.public_batch(batch))
                    action = dict(type='sync', local_id=entry['local_id'], version=entry['version'], request_id=entry['request_id'], **batch['target'])
                result = self.sync(action)
                with self.board.workspace_transaction() as (state, _):
                    batch = state['calendar_sync_batches'][batch_id]
                    entry = next(e for e in batch['entries'] if e['local_id'] == local_id)
                    entry.update(state='synced' if result['ok'] else 'unconfirmed' if result.get('change_state') == 'unconfirmed' else 'failed',
                                 error=result.get('error', ''), stage=result.get('stage', ''))
                    return dict(ok=True, batch=self.public_batch(batch), result=result)
        except Exception as exc:
            return dict(ok=False, stage='batch_step', error=str(exc))

    def change(self, action):
        if not isinstance(action, dict):
            return dict(ok=False, error='日程操作必须为对象。')
        if action.get('type') in {'delete','resolve_delete'}:
            from .calendar_writes import CalendarWrites
            return CalendarWrites(self.scheduling).delete(action) if action['type']=='delete' else CalendarWrites(self.scheduling).resolve(action)
        handlers = {'create': self.save, 'update': self.save, 'sync': self.sync, 'resolve_sync': self.resolve,
                    'resolve_save': self.resolve_save, 'prepare_sync': self.prepare_batch}
        handler = handlers.get(action.get('type'))
        return handler(action) if handler else dict(ok=False, error='不支持的本地日程操作。')

    def resolve_save(self, action):
        attempted = False
        try:
            rid = self.board.validate_request_id(action.get('request_id'))
            original_id = self.board.validate_request_id(action.get('operation_id'))
            uid = identifier(action.get('local_id'))
            if rid == original_id or action.get('confirmed') is not True:
                raise ValueError('请明确核对当前本地记录并使用新编号。')
            with self.scheduling.hold_event(LOCAL, uid):
                with self.board.workspace_transaction() as (state, _):
                    old = self.operations(state).get(original_id)
                    if old and (old.get('local_id') != uid or old.get('type') != 'save'):
                        raise ValueError('原编号不属于此本地保存。')
                    if old and old['state'] == 'saved':
                        return self.result(old, self.view(state, self.require(state, uid)))
                    op = dict(id=original_id, type='save', fingerprint='', local_id=uid,
                              state='observed', stage='committed', error='原保存未登记，已封存编号；不会执行迟到的保存。')
                    self.operations(state)[original_id] = op
                    self.operations(state)[rid] = dict(op, id=rid)
                    attempted = True
                    return self.result(op)
        except Exception as exc:
            return dict(ok=False, change_state='unconfirmed' if attempted else 'not_changed', stage='resolve_save',
                        error=('核对结果保存未确认，请按编号重新读取。' if attempted else '')+str(exc))
