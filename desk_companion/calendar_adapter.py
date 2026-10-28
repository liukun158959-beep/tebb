"""Typed primary-calendar IO through the existing CLI transport; no implicit writes."""
from datetime import datetime
import json
import re
import time

from .calendar_time import TZ, parse_event_time, day_range


class CalendarError(RuntimeError):
    def __init__(self, message, status='read_failed'):
        super().__init__(message)
        self.status = status


def identifier(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or any(c.isspace() for c in value):
        raise ValueError('日历或日程标识无效，请重新读取。')
    return value


def event_view(raw, calendar_id):
    """Whitelist display fields, preserving absolute time and conservative type recognition."""
    eid = identifier(raw.get('event_id'))
    start, end = raw.get('start_time', {}), raw.get('end_time', {})
    timed, start_text, end_text = False, '', ''
    try:
        a = datetime.fromtimestamp(int(start['timestamp']), TZ)
        b = datetime.fromtimestamp(int(end['timestamp']), TZ)
        timed = a < b and not start.get('date') and not end.get('date')
        start_text, end_text = a.isoformat(), b.isoformat()
    except (KeyError, TypeError, ValueError, OSError, OverflowError):
        start_text = str(start.get('date', '')) if isinstance(start, dict) else ''
        end_text = str(end.get('date', '')) if isinstance(end, dict) else ''
    repeat = bool(raw.get('recurrence') or raw.get('recurring_event_id') or raw.get('is_exception'))
    status = raw.get('status', '')
    ordinary = timed and eid.endswith('_0') and not repeat and raw.get('recurrence', '') == ''
    eligible = ordinary and status in {'confirmed', 'tentative'}
    reason = '' if eligible else ('日程已取消' if status == 'cancelled' else
        '重复日程先只读展示' if repeat or not eid.endswith('_0') else
        '全天或时间类型不明，先只读展示' if not timed else '日程类型或状态未确认，先只读展示')
    return dict(calendar_id=calendar_id, event_id=eid, summary=str(raw.get('summary') or '未命名日程'),
        start=start_text, end=end_text, status=status, eligible=eligible, reason=reason,
        verification='deleted' if status == 'cancelled' else 'confirmed', error='', checked_at=datetime.now(TZ).isoformat())


def creation_fields(summary, start, end):
    if not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 120 or any(c in summary for c in '\r\n'):
        raise ValueError('日程标题须为 1–120 字且不能换行。')
    a, b = parse_event_time(start, '开始时间'), parse_event_time(end, '结束时间')
    if a >= b:
        raise ValueError('结束时间必须晚于开始时间。')
    if b <= datetime.now(TZ):
        raise ValueError('不能新建已经结束的安排，请选择现在或未来的时间。')
    return dict(summary=summary.strip(), start=a.astimezone(TZ).isoformat(), end=b.astimezone(TZ).isoformat())


class CalendarAdapter:
    def __init__(self, run=None):
        if run is None:
            from .feishu_tools import _run_lark
            run = _run_lark
        self.run = run

    def _call(self, args, timeout=20):
        try:
            raw = self.run(['calendar', *args, '--as', 'user'], timeout=timeout)
            env = json.loads(raw)
            if env.get('ok') is True and isinstance(env.get('data'), dict):
                return env['data']
            failure = json.dumps(env)
        except Exception as exc:
            failure = str(exc)
        # The existing CLI transport embeds its JSON error in RuntimeError on nonzero exit.
        codes = set()
        decoder = json.JSONDecoder()
        def collect(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {'code', 'error_code', 'http_status', 'status_code'}:
                        try: codes.add(int(item))
                        except (TypeError, ValueError): pass
                    elif isinstance(item, (dict, list)): collect(item)
            elif isinstance(value, list):
                for item in value: collect(item)
        for match in re.finditer(r'\{', failure):
            try: collect(decoder.raw_decode(failure[match.start():])[0])
            except (ValueError, TypeError): pass
        # Official calendar-v4 codes: event missing/deleted is distinct from permission or transport failure.
        status = ('deleted' if 193003 in codes else 'missing' if 193001 in codes else
                  'unavailable' if codes & {403, 191000, 191002, 191003, 193002} else 'read_failed')
        label = {'deleted':'日程已删除', 'missing':'日程不存在', 'unavailable':'日历或日程不可访问',
                 'read_failed':'飞书读取失败，请检查连接和日历授权'}[status]
        raise CalendarError(label + (f'（错误码 {", ".join(map(str, sorted(codes)))}）' if codes else ''), status)

    def primary(self):
        rows = self._call(['calendars', 'primary', '--user-id-type', 'open_id']).get('calendars', [])
        primary = [r for r in rows if isinstance(r, dict)
                   and r.get('calendar', {}).get('type') == 'primary']
        if len(primary) != 1:
            raise CalendarError('未取得唯一的当前用户主日历，请核对飞书登录身份。')
        calendar = primary[0]['calendar']
        return dict(id=identifier(calendar.get('calendar_id')), summary=str(calendar.get('summary', '主日历')),
                    writable=calendar.get('role') in {'owner', 'writer'} and not calendar.get('is_deleted') and not calendar.get('is_third_party'),
                    owner_id=primary[0].get('user_id', ''),
                    identity_verified=bool(primary[0].get('user_id'))
                        and calendar.get('is_deleted', False) is False
                        and calendar.get('is_third_party', False) is False)

    def week(self, calendar_id, day):
        from .schedule_domain import week_range
        a, b = week_range(day)
        data = self._call(['events', 'instance_view', '--calendar-id', identifier(calendar_id),
                          '--user-id-type', 'open_id', '--start-time', str(int(a.timestamp())),
                          '--end-time', str(int(b.timestamp()))])
        if not isinstance(data.get('items'), list):
            raise CalendarError('飞书周日程列表缺失，不能当作本周没有安排。')
        rows = [event_view(row, calendar_id) for row in data['items']]
        for row in rows:
            row.update(editable=False, edit_reason='选择后核对编辑权限及完整参与人')
        return sorted(rows, key=lambda row: (row['start'], row['event_id']))

    def inspect(self, calendar, event_id):
        """Prove organizer + complete personal attendance, never infer it from a week list."""
        from .schedule_domain import personal_time_reason
        deadline = time.monotonic() + 35
        data = self._call(['events', 'get', '--calendar-id', identifier(calendar['id']),
                          '--event-id', identifier(event_id), '--user-id-type', 'open_id'])
        raw = data.get('event')
        if not isinstance(raw, dict) or raw.get('event_id') != event_id:
            raise CalendarError('日程详情或事件身份未确认，请重新读取。')
        view = event_view(raw, calendar['id'])
        reason = personal_time_reason(raw, view, calendar)
        if not reason:
            token, seen = '', set()
            try:
                for _ in range(20):
                    remaining = deadline - time.monotonic()
                    if remaining < 1:
                        raise CalendarError('参与人核对达到时限，名单未完整确认。')
                    args = ['event.attendees', 'list', '--calendar-id', calendar['id'],
                            '--event-id', event_id, '--user-id-type', 'open_id', '--page-size', '100']
                    if token: args += ['--page-token', token]
                    page = self._call(args, timeout=min(20, remaining))
                    if not isinstance(page.get('items'), list) or type(page.get('has_more')) is not bool:
                        raise CalendarError('参与人列表不完整，不能认作无人。')
                    for row in page['items']:
                        if (not isinstance(row, dict) or row.get('type') != 'user'
                            or row.get('user_id') != calendar['owner_id'] or row.get('is_organizer') is not True
                            or any(row.get(k) for k in ('room_id', 'chat_id', 'third_party_email'))
                            or row.get('is_external') is True):
                            reason = '存在其他参与人、会议室或未识别项，本批只读'
                            break
                    if reason or not page['has_more']: break
                    token = page.get('page_token')
                    if not isinstance(token, str) or not token or token in seen:
                        raise CalendarError('参与人分页标记缺失或循环，名单未完整确认。')
                    seen.add(token)
                else:
                    raise CalendarError('参与人分页超过核对上限，名单未完整确认。')
            except Exception as exc:
                reason = str(exc)
        view.update(editable=not bool(reason), edit_reason=reason,
                    edit_checked_at=datetime.now(TZ).isoformat())
        return view

    def patch_time(self, calendar_id, event_id, start, end):
        a, b = parse_event_time(start, '开始时间'), parse_event_time(end, '结束时间')
        body = dict(start_time=dict(timestamp=str(int(a.timestamp())), timezone='Asia/Shanghai'),
                    end_time=dict(timestamp=str(int(b.timestamp())), timezone='Asia/Shanghai'),
                    need_notification=False)
        data = self._call(['events', 'patch', '--calendar-id', identifier(calendar_id),
                          '--event-id', identifier(event_id), '--user-id-type', 'open_id',
                          '--data', json.dumps(body, ensure_ascii=False)])
        raw = data.get('event')
        if not isinstance(raw, dict) or raw.get('event_id') != event_id:
            raise CalendarError('飞书修改回执缺少原事件身份，结果未确认。')
        return event_view(raw, calendar_id)

    def delete(self, calendar_id, event_id):
        self._call(['events','delete','--calendar-id',identifier(calendar_id),'--event-id',identifier(event_id),'--need-notification','true'])

    def patch_details(self, calendar_id, event_id, start, end, summary):
        a,b=parse_event_time(start,'开始'),parse_event_time(end,'结束')
        body=dict(summary=summary,start_time=dict(timestamp=str(int(a.timestamp())),timezone='Asia/Shanghai'),
                  end_time=dict(timestamp=str(int(b.timestamp())),timezone='Asia/Shanghai'),need_notification=False)
        data=self._call(['events','patch','--calendar-id',identifier(calendar_id),'--event-id',identifier(event_id),'--user-id-type','open_id','--data',json.dumps(body,ensure_ascii=False)])
        if not isinstance(data.get('event'),dict) or data['event'].get('event_id')!=event_id:raise CalendarError('修改回执身份未确认。')
        return event_view(data['event'],calendar_id)

    def day(self, calendar_id, day):
        a, b = day_range(day)
        rows = self._call(['events', 'instance_view', '--calendar-id', identifier(calendar_id),
                          '--start-time', str(int(a.timestamp())), '--end-time', str(int(b.timestamp()))]).get('items')
        if not isinstance(rows, list):
            raise CalendarError('飞书日程列表缺失，不能当作当天没有安排。')
        return sorted([event_view(r, calendar_id) for r in rows], key=lambda r: (r['start'], r['event_id']))

    def get(self, calendar_id, event_id):
        data = self._call(['events', 'get', '--calendar-id', identifier(calendar_id), '--event-id', identifier(event_id)])
        if not isinstance(data.get('event'), dict):
            raise CalendarError('飞书日程详情缺失，请重新核对。')
        result = event_view(data['event'], calendar_id)
        if result['event_id'] != event_id:
            raise CalendarError('飞书返回了不同的事件身份，未自动换绑。')
        return result

    def create(self, calendar_id, request_id, fields):
        a, b = parse_event_time(fields['start'], '开始时间'), parse_event_time(fields['end'], '结束时间')
        body = dict(summary=fields['summary'], start_time=dict(timestamp=str(int(a.timestamp())), timezone='Asia/Shanghai'),
                    end_time=dict(timestamp=str(int(b.timestamp())), timezone='Asia/Shanghai'),
                    recurrence='', reminders=[], need_notification=False, vchat=dict(vc_type='no_meeting'))
        data = self._call(['events', 'create', '--calendar-id', identifier(calendar_id),
                          '--idempotency-key', request_id, '--data', json.dumps(body, ensure_ascii=False)])
        if not isinstance(data.get('event'), dict):
            raise CalendarError('飞书返回成功但没有事件身份，创建结果未确认。')
        return event_view(data['event'], calendar_id)
