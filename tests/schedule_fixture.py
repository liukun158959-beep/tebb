"""Offline calendar boundary; never imports a live CLI command."""
import copy
from datetime import datetime, timedelta

from desk_companion.calendar_adapter import CalendarError, event_view
from desk_companion.calendar_time import TZ

DAY = (datetime.now(TZ)+timedelta(days=1)).date().isoformat()
START, END = DAY+'T09:00:00+08:00', DAY+'T10:00:00+08:00'


def event(event_id='existing_0', start=START, end=END, **extra):
    raw = dict(event_id=event_id, summary='测试安排', status='confirmed', recurrence='',
        start_time=dict(timestamp=str(int(datetime.fromisoformat(start).timestamp()))),
        end_time=dict(timestamp=str(int(datetime.fromisoformat(end).timestamp()))))
    raw.update(extra)
    return event_view(raw, 'calendar-one')


class FakeCalendar:
    def __init__(self):
        self.events = {'existing_0': event()}
        self.creates = []
        self.calendar_id = 'calendar-one'
        self.fail = None

    def primary(self):
        return dict(id=self.calendar_id, summary='离线主日历', writable=True)

    def day(self, calendar_id, day):
        if self.fail: raise self.fail
        return copy.deepcopy(list(self.events.values()))

    def get(self, calendar_id, event_id):
        if self.fail: raise self.fail
        if event_id not in self.events: raise CalendarError('日程不存在', 'missing')
        return copy.deepcopy(self.events[event_id])

    def create(self, calendar_id, key, fields):
        self.creates.append(key)
        result = event(f'created-{len(self.creates)}_0', fields['start'], fields['end'])
        result['summary'] = fields['summary']
        self.events[result['event_id']] = copy.deepcopy(result)
        return result


class TimeCalendar(FakeCalendar):
    """Fake remote clock edits; the real adapter's identity/pagination is tested separately."""
    def __init__(self):
        super().__init__()
        self.patches = []
        self.deletes = []

    def primary(self):
        return dict(super().primary(), owner_id='fixture-self', identity_verified=True)

    def week(self, calendar_id, day):
        from desk_companion.schedule_domain import week_range
        a, b = week_range(day)
        if self.fail: raise self.fail
        return [copy.deepcopy(e) for e in self.events.values()
                if datetime.fromisoformat(e['start']) < b and datetime.fromisoformat(e['end']) > a]

    def inspect(self, calendar, event_id):
        return dict(self.get(calendar['id'], event_id), editable=True, edit_reason='',
                    edit_checked_at=datetime.now(TZ).isoformat())

    def patch_time(self, calendar_id, event_id, start, end):
        self.patches.append(dict(calendar_id=calendar_id, event_id=event_id, start=start, end=end))
        old = self.get(calendar_id, event_id)
        current = dict(old, start=start, end=end, checked_at=datetime.now(TZ).isoformat())
        self.events[event_id] = copy.deepcopy(current)
        return current

    def delete(self, calendar_id, event_id):
        self.deletes.append((calendar_id,event_id))
        self.events.pop(event_id,None)

    def patch_details(self, calendar_id, event_id, start, end, summary):
        result=self.patch_time(calendar_id,event_id,start,end)
        result['summary']=summary
        self.events[event_id]=copy.deepcopy(result)
        return result
