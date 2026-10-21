"""Pure work-item/calendar relationships; neither AI runs nor remote execution live here."""
import copy
from datetime import datetime

from .calendar_time import TZ


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
            fresh = event.get('verification') == 'confirmed' and (now-checked).total_seconds() < 300
            if not fresh: unchecked.append(event)
            elif a <= now < b: active.append(event)
            elif a > now: future.append(event)
        except (ValueError, KeyError, TypeError):
            unchecked.append(event)
    candidates = sorted(active, key=lambda r: r['start']) or sorted(future, key=lambda r: r['start'])
    return dict(events=events, next=candidates[0] if candidates else None,
                unchecked=bool(unchecked), previous=unchecked[0] if unchecked else None)
