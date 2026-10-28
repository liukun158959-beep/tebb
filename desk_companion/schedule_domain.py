"""Pure work-item/calendar relationships; neither AI runs nor remote execution live here."""
import copy
from datetime import datetime, timedelta

from .calendar_time import TZ, day_range


def week_range(day):
    start, _ = day_range(day)
    start -= timedelta(days=start.weekday())
    return start, start + timedelta(days=7)


def personal_time_reason(raw, event, calendar):
    """Derived permission, separate from the old association eligibility flag."""
    if not calendar.get('writable') or not calendar.get('identity_verified'):
        return '日历写权限或当前身份未核实'
    owner = calendar.get('owner_id')
    if (not isinstance(owner, str) or not owner or raw.get('organizer_calendar_id') != calendar['id']
        or not isinstance(raw.get('event_organizer'), dict) or raw['event_organizer'].get('user_id') != owner):
        return '尚未核实为本人组织的日程'
    if not event['eligible'] or raw.get('recurrence') != '' or raw.get('is_exception') is not False:
        return event['reason'] or '普通单次类型尚未完整核实'
    if raw.get('source'):
        return '日程来源类型未核实，本批只读'
    a, b = datetime.fromisoformat(event['start']), datetime.fromisoformat(event['end'])
    if a.date() != b.date(): return '跨日日程本批只读'
    if b <= datetime.now(TZ): return '已结束日程本批只读'
    return ''


def same_time(event, start, end):
    from .calendar_time import parse_event_time
    try:
        return (parse_event_time(event['start'], '开始') == parse_event_time(start, '开始')
                and parse_event_time(event['end'], '结束') == parse_event_time(end, '结束'))
    except (ValueError, RuntimeError, KeyError):
        return False


def time_fields(action):
    from .calendar_time import parse_event_time
    a, b = [parse_event_time(action.get(k), k).astimezone(TZ) for k in ('start', 'end')]
    old_a, old_b = [parse_event_time(action.get(k), k).astimezone(TZ) for k in ('original_start', 'original_end')]
    if a >= b or a.date() != b.date(): raise ValueError('起止须在同一北京时间日期，结束晚于开始。')
    if b <= datetime.now(TZ): raise ValueError('调整后的结束时间须晚于当前时间。')
    if old_b <= datetime.now(TZ): raise ValueError('已结束日程本批只读。')
    duration, old_duration = (b-a).total_seconds(), (old_b-old_a).total_seconds()
    if duration < 900 and duration != old_duration:
        raise ValueError('缩放最短 15 分钟，原有短日程仅可保持原时长移动。')
    return dict(start=a.isoformat(), end=b.isoformat(),
                original_start=old_a.isoformat(), original_end=old_b.isoformat())


def time_pending(state, calendar_id, event_id):
    return [op for op in [*state.get('schedule_operations', {}).values(), *state.get('local_calendar_operations',{}).values()]
            if op['type'] in {'retime','delete'} and op.get('calendar_id') == calendar_id and op.get('event_id') == event_id
            and op['state'] in {'prepared', 'calling', 'remote_changed', 'unconfirmed'}]


def require_time_idle(state, calendar_id, event_id):
    if time_pending(state, calendar_id, event_id):
        raise ValueError('此日程有未结束或结果未确认的改期，请先按原编号核对。')


def cache_event(state, event, previous=None):
    """One confirmed observation updates links and every loaded overlapping range."""
    read_version = state['schedule_read_version'] = state.get('schedule_read_version', 0)+1
    state.get('schedule_reads', {}).pop('event:'+event_key(event), None)
    state.get('schedule_reads', {}).pop('event-version:'+event_key(event), None)
    update_event(state, event, read_version)
    for prefix, store in (('day:', 'schedule_days'), ('week:', 'schedule_weeks')):
        dates = set(state.get(store, {})) | {k[len(prefix):] for k in state.get('schedule_reads', {}) if k.startswith(prefix)}
        for day in dates:
            a, b = week_range(day) if store == 'schedule_weeks' else day_range(day)
            cached = state.get(store, {}).get(day)
            def overlaps(row):
                try:
                    start = datetime.fromisoformat(row['start'])
                    end = datetime.fromisoformat(row['end'])
                    if start.tzinfo is None: start = start.replace(tzinfo=TZ)
                    if end.tzinfo is None: end = end.replace(tzinfo=TZ)
                    return start < b and end > a
                except (KeyError, ValueError, TypeError): return False
            known = bool(cached and any(event_key(e) == event_key(event) for e in cached.get('events', [])))
            belongs = overlaps(event) and event.get('verification') not in {'missing', 'deleted'}
            if known or belongs or (previous and overlaps(previous)):
                state.get('schedule_reads', {}).pop(prefix+day, None)
                if cached and (cached.get('calendar') or {}).get('id') == event['calendar_id']:
                    rows = [r for r in cached.get('events', []) if event_key(r) != event_key(event)]
                    if belongs: rows.append(copy.deepcopy(event))
                    cached['events'] = sorted(rows, key=lambda e: (e['start'], e['event_id']))


def require_room(state, item_id):
    if len(links_for(state, item_id)) >= 50:
        raise ValueError('每件事项最多关联 50 场日程。')


def event_key(event):
    return event['calendar_id'] + '/' + event['event_id']


def links_for(state, item_id):
    return [copy.deepcopy(link) for link in state.get('schedule_links', []) if link['item_id'] == item_id]


def attach(state, item_id, event):
    links = state.setdefault('schedule_links', [])
    existing = next((r for r in links if event_key(r['event']) == event_key(event)), None)
    if existing and existing['item_id'] != item_id:
        raise ValueError('此日程已关联其他事项，请先明确解除原关联。')
    if existing:
        existing['event'] = copy.deepcopy(event)
        existing['read_version'] = state.get('schedule_read_version', 0)+1
    else:
        require_room(state, item_id)
        links.append(dict(item_id=item_id, event=copy.deepcopy(event), read_version=state.get('schedule_read_version', 0)+1))


def detach(state, item_id, calendar_id, event_id):
    state['schedule_links'] = [r for r in state.get('schedule_links', [])
        if not (r['item_id'] == item_id and r['event']['calendar_id'] == calendar_id and r['event']['event_id'] == event_id)]


def update_event(state, event, read_version=None):
    reads = state.get('schedule_reads', {})
    key = event_key(event)
    if read_version is not None and read_version > reads.get('event-version:'+key, float('inf')):
        reads.pop('event:'+key, None)
        reads.pop('event-version:'+key, None)
    for link in state.get('schedule_links', []):
        if event_key(link['event']) == event_key(event):
            if read_version is not None and read_version < link.get('read_version', 0):
                continue
            link['event'] = copy.deepcopy(event)
            if read_version is not None: link['read_version'] = read_version


def item_schedule_view(state, item_id):
    events = [r['event'] for r in links_for(state, item_id)]
    now = datetime.now(TZ)
    active, future, unchecked = [], [], []
    for event in events:
        if event.get('verification') in {'deleted', 'missing'} or event.get('status') == 'cancelled':
            continue
        try:
            checked = datetime.fromisoformat(event['checked_at'])
            a, b = datetime.fromisoformat(event['start']), datetime.fromisoformat(event['end'])
            fresh = event.get('verification') == 'confirmed' and (event.get('sync_state') == 'local' or (now-checked).total_seconds() < 300)
            if not fresh: unchecked.append(event)
            elif a <= now < b: active.append(event)
            elif a > now: future.append(event)
        except (ValueError, KeyError, TypeError):
            unchecked.append(event)
    candidates = sorted(active, key=lambda r: r['start']) or sorted(future, key=lambda r: r['start'])
    return dict(events=events, next=candidates[0] if candidates else None,
                unchecked=bool(unchecked), previous=unchecked[0] if unchecked else None)
