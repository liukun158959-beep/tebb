"""Work-item scheduling: durable local receipts around a single explicit remote create."""
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime
import hashlib
import json
import uuid

from . import schedule_domain as domain, work_items
from .board_data import calendar_operation
from .calendar_adapter import CalendarAdapter, CalendarError, creation_fields, day_range, identifier
from .calendar_time import TZ

UNRESOLVED = {'prepared', 'calling', 'remote_created', 'unconfirmed'}
SUCCESS = {'linked', 'unlinked', 'resolved_absent'}


class SchedulingService:
    def __init__(self, work_board, calendar=None):
        self.board = work_board
        self.calendar = calendar or CalendarAdapter()

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
        return dict(ok=True, day=day, item_id=item_id, calendar=cached.get('calendar'),
            events=cached.get('events', []), events_valid=cached.get('valid', False),
            events_error=cached.get('error', ''), checked_at=cached.get('checked_at', ''),
            links=copy.deepcopy(state.get('schedule_links', [])), items=items,
            operations=[self._public_operation(op) for op in ops.values()
                        if (not item_id or op['item_id'] == item_id) and op['state'] in UNRESOLVED],
            operation=self._public_operation(ops.get(request_id)))

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
            except Exception as exc:
                error = str(exc)
            if calendar and links:
                def verify(link):
                    old = link['event']
                    try:
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
                # A confirmed event belongs in already loaded dates immediately. Invalidate
                # overlapping reads started before this commit so they cannot hide it again.
                for day, cached in state.get('schedule_days', {}).items():
                    if (cached.get('calendar') or {}).get('id') != event['calendar_id']:
                        continue
                    a, b = day_range(day)
                    rows = cached.get('events', [])
                    previous = any(domain.event_key(e) == domain.event_key(event) for e in rows)
                    belongs = datetime.fromisoformat(event['start']) < b and datetime.fromisoformat(event['end']) > a
                    if previous or belongs:
                        rows = [e for e in rows if domain.event_key(e) != domain.event_key(event)]
                        if belongs: rows.append(copy.deepcopy(event))
                        cached['events'] = sorted(rows, key=lambda e: (e['start'], e['event_id']))
                        state.get('schedule_reads', {}).pop('day:'+day, None)
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
            with self.board.hold_item(item_id):
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
