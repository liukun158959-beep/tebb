"""Work-item scheduling: durable receipts around explicit remote creation and time edits."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
import copy
from datetime import datetime
import hashlib
import json
import uuid

from . import schedule_domain as domain, work_items
from .board_data import calendar_operation
from .calendar_adapter import CalendarAdapter, CalendarError, creation_fields, day_range, identifier
from .calendar_time import TZ
from .resource_lock import ResourceLock

UNRESOLVED = {'prepared', 'calling', 'remote_created', 'remote_changed', 'unconfirmed'}
SUCCESS = {'linked', 'unlinked', 'resolved_absent', 'retimed', 'observed', 'unchanged'}


class SchedulingService:
    def __init__(self, work_board, calendar=None):
        self.board = work_board
        self.calendar = calendar or CalendarAdapter()
        from .local_calendar import LocalCalendarService
        self.local = LocalCalendarService(self)

    @contextmanager
    def hold_event(self, calendar_id, event_id):
        lock = ResourceLock(f'schedule-event:{self.board.path.resolve()}:{calendar_id}/{event_id}')
        if not lock.acquire(blocking=False):
            raise ValueError('此日程正在调整或核对，请稍后操作。')
        try:
            yield
        finally:
            lock.__exit__()

    @staticmethod
    def _known_event(state, calendar_id, event_id):
        key = calendar_id+'/'+event_id
        rows = [link['event'] for link in state.get('schedule_links', [])]
        for store in ('schedule_weeks', 'schedule_days'):
            for cached in state.get(store, {}).values(): rows.extend(cached.get('events', []))
        rows.extend(op['event'] for op in state.get('schedule_operations', {}).values() if op.get('event'))
        return copy.deepcopy(next((row for row in rows if domain.event_key(row) == key), None))

    def _week_page(self, state, day, request_id=''):
        a, b = domain.week_range(day)
        monday = a.date().isoformat()
        page = self._page(state, monday, request_id=request_id)
        cached = state.get('schedule_weeks', {}).get(monday, {})
        page.update(week=True, week_start=monday, week_end=b.date().isoformat(),
            calendar=cached.get('calendar'), events=copy.deepcopy(cached.get('events', [])),
            events_valid=cached.get('valid', False), events_error=cached.get('error', ''),
            checked_at=cached.get('checked_at', ''),
            operations=[self._public_operation(op) for op in state.get('schedule_operations', {}).values()
                        if op['type'] == 'retime' and op['state'] in UNRESOLVED])
        return self.local.project(page, state, week=True)

    def load_week(self, day, refresh=False, request_id=''):
        try:
            a, _ = domain.week_range(day); monday = a.date().isoformat()
            if request_id: self.board.validate_request_id(request_id)
            if refresh:
                token = uuid.uuid4().hex
                with self.board.workspace_transaction() as (state, _):
                    version = state['schedule_read_version'] = state.get('schedule_read_version', 0)+1
                    state.setdefault('schedule_reads', {})['week:'+monday] = token
                calendar, events, error = None, [], ''
                try:
                    calendar = self.calendar.primary()
                    events = self.calendar.week(calendar['id'], monday)
                    events = self.local.refresh_missing(calendar, events, monday, week=True)
                except Exception as exc:
                    error = str(exc)
                with self.board.workspace_transaction() as (state, _):
                    if state.get('schedule_reads', {}).get('week:'+monday) == token:
                        store = state.setdefault('schedule_weeks', {})
                        old = store.get(monday, {})
                        store[monday] = (dict(old, valid=False, error=error) if error else
                            dict(calendar=calendar, events=events, valid=True, error='', checked_at=datetime.now(TZ).isoformat()))
                        if not error:
                            for event in events: domain.update_event(state, event, version)
                    return self._week_page(state, monday, request_id)
            with self.board.workspace_transaction() as (state, _):
                return self._week_page(state, monday, request_id)
        except Exception as exc:
            return dict(ok=False, stage='read', error=f'周日程读取失败：{exc}。请重新核对。')

    def load_month(self, day, refresh=False, request_id=''):
        """Compose the complete visible month from the existing versioned week caches."""
        from calendar import monthrange
        from datetime import timedelta
        try:
            start, _ = day_range(day)
            first = start.replace(day=1)
            last = start.replace(day=monthrange(start.year, start.month)[1])
            a = domain.week_range(first.date().isoformat())[0]
            b = domain.week_range(last.date().isoformat())[1]
            dates = [(a+timedelta(days=n)).date().isoformat() for n in range(0, (b-a).days, 7)]
            with ThreadPoolExecutor(max_workers=2) as pool:
                pages = list(pool.map(lambda date: self.load_week(date, refresh, request_id), dates))
            usable = [p for p in pages if p.get('ok')]
            if not usable:
                raise ValueError('；'.join(p.get('error', '读取失败') for p in pages))
            page = copy.deepcopy(usable[-1])
            errors = [f"{date}: {p.get('events_error') or p.get('error') or '读取未确认'}"
                      for date, p in zip(dates, pages) if not p.get('ok') or not p.get('events_valid')]
            events = {domain.event_key(e): e for p in usable for e in p['events']}
            operations = {o['id']: o for p in usable for o in p.get('operations', [])}
            page.update(day=day, month=True, week=True, week_start=a.date().isoformat(), week_end=b.date().isoformat(),
                        events=sorted(events.values(), key=lambda e: (e['start'], e['event_id'])),
                        operations=list(operations.values()), events_valid=not errors, events_error='；'.join(errors),
                        checked_at=min((p.get('checked_at', '') for p in usable), default=''))
            return page
        except Exception as exc:
            return dict(ok=False, stage='month_read', error=f'月日程读取失败：{exc}')

    def inspect_event(self, calendar_id, event_id):
        from .local_calendar import LOCAL
        if calendar_id == LOCAL:
            return self.local.inspect(event_id)
        event = None
        try:
            identifier(calendar_id); identifier(event_id)
            token, key = uuid.uuid4().hex, calendar_id+'/'+event_id
            with self.board.workspace_transaction() as (state, _):
                version = state['schedule_read_version'] = state.get('schedule_read_version', 0)+1
                state.setdefault('schedule_reads', {})['event:'+key] = token
                state['schedule_reads']['event-version:'+key] = version
                previous = self._known_event(state, calendar_id, event_id)
            calendar = self.calendar.primary()
            if calendar['id'] != calendar_id:
                raise CalendarError('当前主日历已变化，请重新读取，未自动换绑。', 'unavailable')
            event = self.calendar.inspect(calendar, event_id)
            with self.board.workspace_transaction() as (state, _):
                link = next((r for r in state.get('schedule_links', []) if domain.event_key(r['event']) == key), None)
                if link and work_items.require_item(state, link['item_id'])['archived']:
                    event.update(editable=False, edit_reason='关联事项已归档，请先恢复事项。')
                if domain.time_pending(state, calendar_id, event_id):
                    event.update(editable=False, edit_reason='改期执行中或结果未确认，请先核对原操作。')
                if state.get('schedule_reads', {}).get('event:'+key) == token:
                    domain.cache_event(state, event, previous)
                else:
                    event = self._known_event(state, calendar_id, event_id) or event
            return dict(ok=True, event=event)
        except Exception as exc:
            if 'previous' in locals() and previous:
                event = dict(previous, editable=False, edit_reason=str(exc),
                             verification=getattr(exc, 'status', 'read_failed'), error=str(exc))
                try:
                    with self.board.workspace_transaction() as (state, _):
                        if state.get('schedule_reads', {}).get('event:'+key) == token:
                            domain.cache_event(state, event)
                except Exception: pass
            return dict(ok=False, stage='inspect', event=event, error=str(exc))

    def _time_result(self, operation, day):
        page = self.load_week(day, request_id=operation['id'])
        successful = operation['state'] in {'retimed', 'observed', 'unchanged'}
        if not page['ok']:
            return dict(ok=False, operation=self._public_operation(operation), request_id=operation['id'],
                stage='refresh', change_state='committed' if successful else 'unconfirmed',
                error='日程操作已有回执，但页面读取失败，请按原编号核对。')
        page.update(ok=successful, operation=self._public_operation(operation), request_id=operation['id'],
            change_state='committed' if successful else 'unconfirmed' if operation['state'] in UNRESOLVED else 'not_changed',
            stage=operation['stage'], conflict=operation.get('conflict', False), error=operation.get('error', ''))
        return page

    def _commit_time(self, operation, event, previous=None):
        with self.board.workspace_transaction() as (state, _):
            domain.cache_event(state, event, previous)
            if operation.get('item_id'):
                work_items.touch_item(work_items.require_item(state, operation['item_id']))
            operation.update(state='retimed', stage='committed', event=event,
                             execution_ended_at=datetime.now(TZ).isoformat(), error='')
            state.setdefault('schedule_operations', {})[operation['id']] = copy.deepcopy(operation)

    def _change_time(self, action):
        operation, writing, remote_started = None, False, False
        leases = ExitStack()
        try:
            request_id = self.board.validate_request_id(action.get('request_id'))
            calendar_id, event_id = identifier(action.get('calendar_id')), identifier(action.get('event_id'))
            day = action.get('day'); domain.week_range(day)
            fingerprint = hashlib.sha256(json.dumps(action, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            leases.enter_context(self.hold_event(calendar_id, event_id))
            with self.board.workspace_transaction() as (state, _):
                existing = copy.deepcopy(state.get('schedule_operations', {}).get(request_id))
                link = next((r for r in state.get('schedule_links', [])
                             if domain.event_key(r['event']) == calendar_id+'/'+event_id), None)
                item_id = link['item_id'] if link else ''
            if existing:
                if existing['fingerprint'] != fingerprint:
                    raise ValueError('此操作编号已用于其他日程请求，未再次修改。')
                return self._time_result(existing, day)
            if item_id: leases.enter_context(self.board.hold_item(item_id))
            with self.board.workspace_transaction() as (state, _):
                if item_id and work_items.require_item(state, item_id)['archived'] and action['type'] != 'reconcile_time':
                    raise ValueError('请先恢复归档事项，再调整日程。')
                if action['type'] == 'retime': domain.require_time_idle(state, calendar_id, event_id)
            if action['type'] == 'reconcile_time':
                return self._reconcile_time(action, fingerprint, item_id)
            fields = domain.time_fields(action)
            with calendar_operation():
                operation = dict(id=request_id, fingerprint=fingerprint, type='retime', item_id=item_id,
                    calendar_id=calendar_id, event_id=event_id, state='prepared', stage='prepare', error='',
                    created_at=datetime.now(TZ).isoformat(), **fields)
                writing = True; self._save_operation(operation); writing = False
                operation['stage'] = 'preflight'
                calendar = self.calendar.primary()
                if calendar['id'] != calendar_id: raise ValueError('主日历已变化，请重新读取。')
                current = self.calendar.inspect(calendar, event_id)
                operation['event'] = current
                if not current.get('editable'): raise ValueError(current.get('edit_reason') or '编辑权限未核实。')
                if not domain.same_time(current, fields['original_start'], fields['original_end']):
                    operation['conflict'] = True
                    with self.board.workspace_transaction() as (state, _): domain.cache_event(state, current)
                    raise ValueError('远端时间已变化，已保留最新时间，请重新调整。')
                summary=action.get('summary')
                if summary is not None and action.get('original_summary')!=current['summary']:raise ValueError('远端标题已变化，请重新读取后修改。')
                if summary is not None and (not isinstance(summary,str) or not 1<=len(summary.strip())<=120):raise ValueError('日程标题须为1至120字。')
                if domain.same_time(current, fields['start'], fields['end']) and (summary is None or summary==current['summary']):
                    operation.update(state='unchanged', stage='committed')
                    self._save_operation(operation)
                    return self._time_result(operation, day)
                identity = self.calendar.primary()
                if (identity['id'], identity.get('owner_id')) != (calendar['id'], calendar.get('owner_id')):
                    raise ValueError('飞书身份已变化，未发送改期，请重新读取。')
                operation.update(state='calling', stage='remote')
                writing = True; self._save_operation(operation); writing = False
                remote_started = True
                acknowledged = (self.calendar.patch_details(calendar_id,event_id,fields['start'],fields['end'],summary.strip()) if summary is not None else self.calendar.patch_time(calendar_id, event_id, fields['start'], fields['end']))
                operation.update(state='remote_changed', stage='verify', event=acknowledged)
                writing = True; self._save_operation(operation); writing = False
                verified = self.calendar.get(calendar_id, event_id)
                if not domain.same_time(verified, fields['start'], fields['end']) or summary is not None and verified['summary']!=summary.strip():
                    operation['current_event'] = verified
                    raise ValueError('远端回执后读取到不同时间，修改结果未完整确认，请先核对。')
                verified.update(editable=current['editable'], edit_reason='', edit_checked_at=verified['checked_at'])
                operation['stage'] = 'commit'
                writing = True; self._commit_time(operation, verified, current); writing = False
                return self._time_result(operation, day)
        except Exception as exc:
            if operation:
                try:
                    with self.board.workspace_transaction() as (state, _):
                        saved = copy.deepcopy(state.get('schedule_operations', {}).get(operation['id']))
                    if saved and saved['state'] in SUCCESS: return self._time_result(saved, day)
                except Exception: pass
                operation.update(state='unconfirmed' if remote_started or writing else 'not_started',
                    error=str(exc), execution_ended_at=datetime.now(TZ).isoformat())
                try: self._save_operation(operation)
                except Exception: pass
                return self._time_result(operation, day)
            return dict(ok=False, stage='admission', change_state='not_changed', error=str(exc))
        finally:
            leases.close()

    def _reconcile_time(self, action, fingerprint, item_id):
        """Explicitly adopt a newly observed time; never claim an unknown request did not execute."""
        if action.get('confirmed') is not True:
            raise ValueError('请明确确认以当前核对时间结束本轮等待；不会重发改期。')
        original_id = self.board.validate_request_id(action.get('operation_id'))
        if original_id == action['request_id']: raise ValueError('核对须使用新的操作编号。')
        with self.board.workspace_transaction() as (state, _):
            original = copy.deepcopy(state.get('schedule_operations', {}).get(original_id))
        if original and (original['type'] != 'retime' or original['calendar_id'] != action['calendar_id']
                         or original['event_id'] != action['event_id']):
            raise ValueError('原操作与当前日程不匹配。')
        calendar = self.calendar.primary()
        if calendar['id'] != action['calendar_id']: raise ValueError('当前主日历已变化，请核对原身份。')
        try:
            event = self.calendar.get(action['calendar_id'], action['event_id'])
        except CalendarError as exc:
            if exc.status not in {'missing', 'deleted'} or action.get('verification') != exc.status:
                raise
            with self.board.workspace_transaction() as (state, _):
                previous = self._known_event(state, action['calendar_id'], action['event_id'])
            event = dict(previous or dict(calendar_id=action['calendar_id'], event_id=action['event_id'],
                summary='已失效日程', start=action.get('original_start', ''), end=action.get('original_end', ''),
                status='', eligible=False, reason='日程已失效'),
                verification=exc.status, checked_at=datetime.now(TZ).isoformat(), editable=False,
                edit_reason=str(exc), error=str(exc))
        if (event['verification'] not in {'missing', 'deleted'} and
            not domain.same_time(event, action.get('original_start'), action.get('original_end'))):
            raise ValueError('核对期间远端时间又有变化，请重新读取后确认。')
        operation = dict(id=action['request_id'], fingerprint=fingerprint, type='reconcile_time', item_id=item_id,
            calendar_id=action['calendar_id'], event_id=action['event_id'], state='observed', stage='committed',
            original_id=original_id, event=event, error='', created_at=datetime.now(TZ).isoformat(),
            notice='当前时间已核对；这不证明原修改未执行，不会重发改期。')
        try:
            with self.board.workspace_transaction() as (state, _):
                domain.cache_event(state, event)
                operations = state.setdefault('schedule_operations', {})
                old = operations.get(original_id) or dict(id=original_id, fingerprint='', type='retime', item_id=item_id,
                    calendar_id=action['calendar_id'], event_id=action['event_id'])
                if old.get('state') != 'retimed':
                    old.update(state='observed', stage='committed', event=event, resolved_by=operation['id'],
                               execution_result='unconfirmed', error='当前时间已核对，原执行结果未确认，不会重放。')
                    operations[original_id] = old
                operations[operation['id']] = copy.deepcopy(operation)
        except Exception as exc:
            try:
                with self.board.workspace_transaction() as (state, _):
                    saved = state.get('schedule_operations', {}).get(operation['id'])
                if saved and saved['state'] == 'observed': return self._time_result(saved, action['day'])
            except Exception: pass
            return dict(ok=False, stage='commit', change_state='unconfirmed', request_id=operation['id'],
                        operation=self._public_operation(operation), error='核对登记结果未确认，请按原编号读取。'+str(exc))
        return self._time_result(operation, action['day'])

    @staticmethod
    def _public_operation(operation):
        if not operation: return None
        result = copy.deepcopy(operation)
        result.pop('fingerprint', None)
        return result

    def _page(self, state, day, item_id='', request_id=''):
        cached = copy.deepcopy(state.get('schedule_days', {}).get(day, {}))
        ops = state.get('schedule_operations', {})
        items = [dict(id=i['id'], title=i['title'], archived=i['archived'], version=i['version'])
                 for i in state.get('work_items', [])]
        page = dict(ok=True, day=day, item_id=item_id, calendar=cached.get('calendar'),
            events=cached.get('events', []), events_valid=cached.get('valid', False),
            events_error=cached.get('error', ''), checked_at=cached.get('checked_at', ''),
            links=copy.deepcopy(state.get('schedule_links', [])), items=items,
            operations=[self._public_operation(op) for op in ops.values()
                        if (not item_id or op['item_id'] == item_id) and op['state'] in UNRESOLVED],
            operation=self._public_operation(ops.get(request_id) or state.get('local_calendar_operations', {}).get(request_id)))
        return self.local.project(page, state)

    def _read(self, day, item_id='', request_id=''):
        with self.board.workspace_transaction() as (state, _):
            return self._page(state, day, item_id, request_id)

    def load(self, day, item_id='', refresh=False, request_id=''):
        try:
            day_range(day)
            if request_id: self.board.validate_request_id(request_id)
            if not refresh: return self._read(day, item_id, request_id)
            token = uuid.uuid4().hex
            with self.board.workspace_transaction() as (state, _):
                if item_id: work_items.require_item(state, item_id)
                reads = state.setdefault('schedule_reads', {})
                read_version = state['schedule_read_version'] = state.get('schedule_read_version', 0)+1
                reads['day:'+day] = token
                if item_id: reads['item:'+item_id] = token
                links = domain.links_for(state, item_id) if item_id else []
            calendar, events, error = None, [], ''
            checked = []
            try:
                calendar = self.calendar.primary()
                events = self.calendar.day(calendar['id'], day)
                events = self.local.refresh_missing(calendar, events, day)
            except Exception as exc:
                error = str(exc)
            if calendar and links:
                def verify(link):
                    old = link['event']
                    try:
                        from .local_calendar import LOCAL
                        if old['calendar_id'] == LOCAL:
                            result = self.local.inspect(old['event_id'])
                            if result.get('event'):
                                return result['event']
                            raise ValueError(result.get('error', '本地日程核对失败。'))
                        if old['calendar_id'] != calendar['id']:
                            raise CalendarError('当前登录的主日历与原安排不同，未自动换绑。', 'unavailable')
                        return self.calendar.get(old['calendar_id'], old['event_id'])
                    except Exception as exc:
                        return dict(old, verification=getattr(exc, 'status', 'read_failed'), error=str(exc))
                with ThreadPoolExecutor(max_workers=4) as pool:
                    checked = list(pool.map(verify, links))
            elif links and error:
                checked = [dict(link['event'], verification='read_failed', error=error) for link in links]
            with self.board.workspace_transaction() as (state, _):
                reads = state.get('schedule_reads', {})
                if reads.get('day:'+day) == token:
                    previous = state.setdefault('schedule_days', {}).get(day, {})
                    state['schedule_days'][day] = (dict(previous, valid=False, error=error) if error else
                        dict(calendar=calendar, events=events, valid=True, error='', checked_at=datetime.now(TZ).isoformat()))
                    if not error:
                        for event in events: domain.update_event(state, event, read_version)
                if item_id and reads.get('item:'+item_id) == token:
                    for event in checked: domain.update_event(state, event, read_version)
                return self._page(state, day, item_id, request_id)
        except Exception as exc:
            return dict(ok=False, stage='read', error=f'日程读取失败：{exc}。请重新核对。')

    def _save_operation(self, operation):
        with self.board.workspace_transaction() as (state, _):
            existing = state.get('schedule_operations', {}).get(operation['id'])
            if existing and existing['state'] in SUCCESS | {'resolved'} and operation['state'] not in SUCCESS:
                return  # A late failure must never erase a confirmed commit or explicit resolution.
            state.setdefault('schedule_operations', {})[operation['id']] = copy.deepcopy(operation)

    def _finish(self, operation, event=None, original=None):
        """Commit relationships, item version and receipt together; no network under this transaction."""
        with self.board.workspace_transaction() as (state, _):
            item = work_items.require_item(state, operation['item_id'])
            kind = operation['type']
            if event:
                domain.attach(state, item['id'], event)
                work_items.touch_item(item)
                operation.update(state='linked', event=event)
                domain.cache_event(state, event)
            elif kind == 'unlink':
                domain.detach(state, item['id'], operation['calendar_id'], operation['event_id'])
                work_items.touch_item(item)
                operation['state'] = 'unlinked'
            else:
                operation['state'] = 'resolved_absent'
            operation.update(stage='committed', error='')
            if original:
                old = state['schedule_operations'][original['id']]
                old.update(state='resolved', resolved_by=operation['id'])
            state.setdefault('schedule_operations', {})[operation['id']] = copy.deepcopy(operation)

    def _result(self, operation, day):
        page = self.load(day, operation['item_id'], request_id=operation['id'])
        if not page['ok']:
            return dict(ok=False, stage='refresh', change_state='committed' if operation['state'] in SUCCESS else 'unconfirmed',
                operation=self._public_operation(operation), request_id=operation['id'],
                error='操作已处理，但页面读取失败。请按原操作编号重新读取核对。')
        page.update(ok=operation['state'] in SUCCESS, request_id=operation['id'], stage=operation['stage'],
            change_state='committed' if operation['state'] in SUCCESS else
                'unconfirmed' if operation['state'] in UNRESOLVED else 'not_changed',
            error=operation.get('error') or ('' if operation['state'] in SUCCESS else '原操作未完整确认，请先核对；不会自动重发。'))
        return page

    def change(self, action):
        if isinstance(action,dict) and action.get('type')=='delete':
            from .calendar_writes import CalendarWrites
            return CalendarWrites(self).delete(action)
        from .local_calendar import LOCAL
        if isinstance(action, dict) and action.get('calendar_id') == LOCAL:
            if action.get('type') in {'retime', 'reconcile_time'}:
                return self.local.retime(action)
            if action.get('type') in {'link', 'unlink'}:
                return self.local.link(action)
        if isinstance(action, dict) and action.get('type') in {'retime', 'reconcile_time'}:
            return self._change_time(action)
        operation, remote_started, event, local_commit, local_write = None, False, None, False, False
        day = datetime.now(TZ).date().isoformat()
        try:
            if not isinstance(action, dict): raise ValueError('日程操作必须是对象。')
            request_id = self.board.validate_request_id(action.get('request_id'))
            item_id = identifier(action.get('item_id'))
            kind = action.get('type')
            if kind not in {'create', 'link', 'unlink', 'resolve'}: raise ValueError('不支持的日程操作。')
            day = action.get('day', day); day_range(day)
            fingerprint = hashlib.sha256(json.dumps(action, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            with self.board.hold_item(item_id), ExitStack() as event_leases:
                if kind in {'link', 'unlink'} or (kind == 'resolve' and action.get('event_id')):
                    calendar_id, event_id = identifier(action.get('calendar_id')), identifier(action.get('event_id'))
                    event_leases.enter_context(self.hold_event(calendar_id, event_id))
                    with self.board.workspace_transaction() as (state, _):
                        domain.require_time_idle(state, calendar_id, event_id)
                with self.board.workspace_transaction() as (state, _):
                    ops = state.setdefault('schedule_operations', {})
                    existing = ops.get(request_id)
                    if existing and existing['fingerprint'] != fingerprint:
                        raise ValueError('此操作编号已用于其他日程请求。')
                    if existing:
                        operation = copy.deepcopy(existing)
                    else:
                        item = work_items.require_item(state, item_id)
                        work_items.check_version(item, action.get('version'))
                        if item['archived'] and not (kind == 'resolve' and action.get('resolution') == 'absent'):
                            raise ValueError('请先恢复归档事项，再新增或调整安排。')
                        if kind == 'create' and any(op['item_id'] == item_id and op['type'] == 'create'
                                                   and op['state'] in UNRESOLVED for op in ops.values()):
                            raise ValueError('此事项有创建结果未确认的安排，请先核对原操作。')
                        if kind == 'create': domain.require_room(state, item_id)
                if existing: return self._result(operation, day)  # Never replay remote calls, including after a restart.
                fields = creation_fields(action.get('summary'), action.get('start'), action.get('end')) if kind == 'create' else {}
                original = None
                if kind == 'resolve':
                    original_id = self.board.validate_request_id(action.get('operation_id'))
                    if original_id == request_id: raise ValueError('核对操作须使用新的操作编号。')
                    if action.get('resolution') not in {'link', 'absent'}:
                        raise ValueError('请选择明确关联已有日程或确认没有有效日程。')
                    if action.get('resolution') == 'absent' and action.get('confirmed') is not True:
                        raise ValueError('请先核对飞书并明确确认没有有效日程。')
                    with self.board.workspace_transaction() as (state, _):
                        original = copy.deepcopy(state.get('schedule_operations', {}).get(original_id))
                    if original and (original['item_id'] != item_id or original['type'] != 'create' or original['state'] not in UNRESOLVED):
                        raise ValueError('原创建操作不存在或已处理，请重新读取。')
                    if not original:
                        # A disconnected request may still be waiting to enter the service.
                        # Reserve its identity under the same item lease before reconciliation.
                        original = dict(id=original_id, fingerprint='', item_id=item_id, type='create',
                            state='unconfirmed', stage='prepare', error='原请求未登记，已保留编号等待明确核对。',
                            created_at=datetime.now(TZ).isoformat())
                        self._save_operation(original)
                operation = dict(id=request_id, fingerprint=fingerprint, item_id=item_id, type=kind,
                    state='prepared', stage='prepare', error='', created_at=datetime.now(TZ).isoformat(), **fields)
                local_write = True
                self._save_operation(operation)  # Confirm durable operation identity before any remote write.
                local_write = False
                if kind == 'unlink':
                    operation.update(calendar_id=identifier(action.get('calendar_id')), event_id=identifier(action.get('event_id')))
                    local_commit = True
                    self._finish(operation)
                elif kind == 'resolve' and action.get('resolution') == 'absent':
                    if action.get('confirmed') is not True: raise ValueError('请先核对飞书并明确确认没有有效日程。')
                    if original.get('event'):
                        known = original['event']
                        if self.calendar.primary()['id'] != known['calendar_id']:
                            raise ValueError('当前主日历与原安排不同，请先核对原登录身份。')
                        try:
                            current = self.calendar.get(known['calendar_id'], known['event_id'])
                            if current['verification'] != 'deleted': raise ValueError('已知远端日程仍存在，请关联它；不能将其当作未创建。')
                        except CalendarError as exc:
                            if exc.status not in {'deleted', 'missing'}: raise
                    local_commit = True
                    self._finish(operation, original=original)
                elif kind == 'create':
                    with calendar_operation():
                        calendar = self.calendar.primary()
                        if not calendar['writable']: raise ValueError('当前主日历不可写，未创建安排。')
                        operation.update(calendar_id=calendar['id'], state='calling', stage='remote',
                            idempotency_key=str(uuid.uuid5(uuid.NAMESPACE_URL, 'tebb:schedule:'+request_id)))
                        local_write = True
                        self._save_operation(operation)  # A crash after this point is unresolved, never automatically retried.
                        local_write = False
                        remote_started = True
                        event = self.calendar.create(calendar['id'], operation['idempotency_key'], fields)
                        operation.update(state='remote_created', stage='association', event=event)
                        self._save_operation(operation)  # Preserve the remote fact even if relationship commit fails.
                        if not event['eligible']: raise ValueError('远端已创建，但事件类型未确认；请核对后关联。')
                        local_commit = True
                        self._finish(operation, event)
                else:
                    calendar = self.calendar.primary()
                    target_calendar = identifier(action.get('calendar_id'))
                    if calendar['id'] != target_calendar: raise ValueError('当前主日历已变化，请重新读取；未自动换绑。')
                    if original and original.get('event') and (original['event']['calendar_id'] != target_calendar or original['event']['event_id'] != action.get('event_id')):
                        raise ValueError('原操作已有已知日程，请核对并关联该日程，不能静默替换。')
                    event = self.calendar.get(target_calendar, identifier(action.get('event_id')))
                    if not event['eligible']: raise ValueError(event['reason'])
                    local_commit = True
                    self._finish(operation, event, original)
                return self._result(operation, day)
        except Exception as exc:
            if operation:
                try:
                    with self.board.workspace_transaction() as (state, _):
                        saved = copy.deepcopy(state.get('schedule_operations', {}).get(operation['id']))
                    if saved and saved['state'] in SUCCESS:
                        return self._result(saved, day)
                except Exception:
                    pass
                # Only creation can have an external side effect. Local save acknowledgement may also be lost.
                uncertain = remote_started or local_write or (local_commit and not isinstance(exc, ValueError))
                operation.update(state='unconfirmed' if uncertain else 'not_started', error=str(exc))
                if event and remote_started: operation['event'] = event
                try: self._save_operation(operation)
                except Exception: pass  # The previously committed phase remains available; never issue another create.
                return dict(ok=False, change_state='unconfirmed' if uncertain else 'not_changed',
                    remote_state='unconfirmed' if remote_started else 'not_started',
                    stage=operation['stage'], request_id=operation['id'], operation=self._public_operation(operation),
                    error=('创建或关联结果未完整确认，请先核对。' if remote_started else '日程操作未完成。')+str(exc))
            return dict(ok=False, change_state='not_changed', stage='admission', conflict=getattr(exc, 'conflict', False), error=str(exc))
