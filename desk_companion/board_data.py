"""看板数据：解析 lark-cli JSON 信封，失败不装空。"""
from __future__ import annotations

from .paths import data_root

import json
import re
from contextlib import contextmanager
from functools import wraps
from .resource_lock import ResourceLock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .feishu_tools import AUTH_HINT, _run_lark

TZ = timezone(timedelta(hours=8))
TODAY_KEYS = (
    "date",
    "fetched_at",
    "agenda",
    "tasks",
    "summary",
    "summary_at",
    "doc_url",
)
_STORE = ResourceLock('today-board-store')
_OPERATION = ResourceLock('today-board-operation')


class BoardChangeError(RuntimeError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


@contextmanager
def _operation():
    if not _OPERATION.acquire(blocking=False):
        raise BoardChangeError('看板正在更新，完成后再操作；已有当天缓存仍可读取。',
            change_state='not_changed', stage='admission', remote_state='not_started', cache_state='not_started')
    try:
        yield
    finally:
        _OPERATION.__exit__()


def _synced(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        with _operation():
            return fn(*args, **kwargs)
    return run


def _assert_day(day):
    if day != today_date():
        raise BoardChangeError('操作期间已跨日，未将旧查询写入今日缓存。请重新加载核对，不要重复远端修改。',
            change_state='not_changed', stage='day_change', cache_state='not_changed')


def _merge_sections(day, sections):
    with _STORE:
        _assert_day(day)
        latest = _read_today_file()
        if latest is None or latest['date'] != day:
            latest = dict(date=day, fetched_at='', agenda=dict(ok=True, items=[]),
                          tasks=dict(ok=True, items=[]), summary='', summary_at='', doc_url='')
        snap = dict(latest, **sections, fetched_at=datetime.now(TZ).isoformat(timespec='seconds'))
        _write_today_file(snap)
        return snap


def today_path() -> Path:
    return data_root() / "memory" / "today.json"


def today_date() -> str:
    return datetime.now(TZ).date().isoformat()


def week_range() -> tuple[datetime, datetime]:
    """东八区本周一 00:00 到此刻。"""
    now = datetime.now(TZ)
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday, now


def fetch_week_feishu(monday: datetime, now: datetime) -> dict:
    start = monday.strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    agenda = _section(
        lambda: parse_agenda(
            _lark_data(["calendar", "+agenda", "--start", start, "--end", end]),
            with_date=True,
        )
    )
    tasks = _section(
        lambda: parse_tasks(
            _lark_data(["task", "+get-my-tasks", "--complete=false", "--page-all"])
        )
    )
    return {"agenda": agenda, "tasks": tasks}


def fetch_board() -> dict:
    agenda = _section(lambda: parse_agenda(_lark_data(["calendar", "+agenda"])))
    return {"agenda": agenda, "tasks": _open_tasks_due_today()}


def _open_tasks_due_today() -> dict:
    due = datetime.now(TZ).replace(hour=23, minute=59, second=59, microsecond=0)
    due_s = due.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    return _section(
        lambda: parse_tasks(
            _lark_data(["task", "+get-my-tasks", "--complete=false", f"--due-end={due_s}"])
        )
    )


def load_today_snapshot(*, refresh: bool) -> dict:
    """当天缓存立即可读，远端刷新单独非阻塞准入。"""
    day = today_date()
    cached = _read_today_file()
    if not refresh and cached is not None and cached['date'] == day:
        return cached
    with _operation():
        # Recheck after admission; never return yesterday as today's snapshot.
        day = today_date()
        cached = _read_today_file()
        if not refresh and cached is not None and cached['date'] == day:
            return cached
        board = fetch_board()
        return _merge_sections(day, board)


def save_today_fields(**fields) -> dict:
    """Summary/link writers merge under a short storage lock, never the network lease."""
    allowed = {'summary', 'summary_at', 'doc_url'}
    unknown = [key for key in fields if key not in allowed]
    if unknown:
        raise RuntimeError(f"今日快照不能改这些键：{', '.join(unknown)}。")
    with _STORE:
        day = today_date()
        cached = _read_today_file()
        if cached is None:
            raise RuntimeError('还没有今日快照。先打开看板「今日」页或点刷新，再生成本周复盘。')
        if cached['date'] != day:
            raise RuntimeError(f"今日快照是 {cached['date']}，今天已是 {day}。先点「刷新」拉今天的日程。")
        snap = dict(cached, **fields)
        _assert_day(day)
        _write_today_file(snap)
        return snap


def _read_today_file() -> dict | None:
    with _STORE:
        path = today_path()
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f'{path} 不是合法 JSON。删掉该文件后到看板「今日」点刷新。') from exc
        return _validate_today(raw, path)


def _write_today_file(snap: dict) -> None:
    from .video import atomic_write
    clean = _validate_today(snap, today_path())
    with _STORE:
        try:
            atomic_write(today_path(), clean)
        except Exception as exc:
            raise BoardChangeError(f'看板缓存保存结果未确认：{exc}。请重新读取核对，不要重复远端修改。',
                change_state='unconfirmed', stage='cache_save', cache_state='unconfirmed') from exc


def _validate_today(raw, path: Path) -> dict:
    if not isinstance(raw, dict):
        raise RuntimeError(f"{path} 根节点必须是对象。删掉该文件后点刷新。")
    missing = [key for key in TODAY_KEYS if key not in raw]
    if missing:
        raise RuntimeError(
            f"{path} 缺少字段 {', '.join(missing)}。删掉该文件后点刷新。"
        )
    date = raw["date"]
    if type(date) is not str:
        raise RuntimeError(f"{path} 的 date 必须是字符串。删掉该文件后点刷新。")
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise RuntimeError(
            f"{path} 的 date={date!r} 必须是 YYYY-MM-DD。删掉该文件后点刷新。"
        ) from exc
    for key in ("fetched_at", "summary", "summary_at", "doc_url"):
        if type(raw[key]) is not str:
            raise RuntimeError(f"{path} 的 {key} 必须是字符串。删掉该文件后点刷新。")
    agenda = _validate_section(raw["agenda"], "agenda", path)
    tasks = _validate_section(raw["tasks"], "tasks", path)
    return {
        "date": date,
        "fetched_at": raw["fetched_at"],
        "agenda": agenda,
        "tasks": tasks,
        "summary": raw["summary"],
        "summary_at": raw["summary_at"],
        "doc_url": raw["doc_url"],
    }


def _validate_section(section, name: str, path: Path) -> dict:
    if not isinstance(section, dict):
        raise RuntimeError(f"{path} 的 {name} 必须是对象。删掉该文件后点刷新。")
    if "ok" not in section:
        raise RuntimeError(f"{path} 的 {name} 缺少 ok。删掉该文件后点刷新。")
    if type(section["ok"]) is not bool:
        raise RuntimeError(f"{path} 的 {name}.ok 必须是 true/false。删掉该文件后点刷新。")
    items = section.get("items", [])
    if not isinstance(items, list):
        raise RuntimeError(f"{path} 的 {name}.items 必须是列表。删掉该文件后点刷新。")
    out = {"ok": section["ok"], "items": items}
    if not section["ok"]:
        error = section.get("error")
        if type(error) is not str or not error.strip():
            raise RuntimeError(
                f"{path} 的 {name} 失败时必须有非空 error。删掉该文件后点刷新。"
            )
        out["error"] = error
    return out


def _lark_data(args: list[str]):
    raw = _run_lark(args)
    try:
        env = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"lark-cli 未返回 JSON。{AUTH_HINT}\n{raw}") from exc
    if not isinstance(env, dict) or env.get("ok") is not True:
        raise RuntimeError(f"lark-cli 返回失败信封。{AUTH_HINT}\n{raw}")
    if "data" not in env:
        raise RuntimeError(f"lark-cli 成功信封缺少 data。\n{raw}")
    return env["data"]


def _section(loader) -> dict:
    try:
        return {"ok": True, "items": loader()}
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc), "items": []}


def parse_agenda(data, *, with_date: bool = False) -> list[dict]:
    items = _as_items(data, "日程")
    out = []
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError("日程条目不是对象。")
        summary = item.get("summary") or item.get("title")
        if not summary:
            raise RuntimeError(f"日程缺少标题，字段: {list(item.keys())}")
        start = _event_time(item, "start", with_date=with_date)
        if not start:
            raise RuntimeError(f"日程「{summary}」缺少开始时间，字段: {list(item.keys())}")
        end = _event_time(item, "end", with_date=with_date)
        if not with_date:
            start, end = _span_labels(item, start, end)
        out.append(
            {
                "summary": str(summary),
                "start": start,
                "end": end,
                "event_id": _event_id(item, str(summary)),
            }
        )
    return out


def parse_tasks(data) -> list[dict]:
    items = _as_items(data, "待办")
    out = []
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError("待办条目不是对象。")
        summary = item.get("summary")
        if not summary:
            raise RuntimeError(f"待办缺少 summary，字段: {list(item.keys())}")
        out.append(
            {
                "summary": str(summary),
                "due_at": item.get("due_at") or "",
                "url": item.get("url") or "",
                "guid": _task_guid(item, str(summary)),
            }
        )
    return out


def _task_guid(item: dict, summary: str) -> str:
    raw = item.get("guid")
    if raw is None or raw == "":
        return ""
    if type(raw) is not str:
        raise RuntimeError(f"待办「{summary}」的 guid 必须是字符串。")
    return raw.strip()


def _as_items(data, label: str) -> list:
    if data in (None, [], {}):
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "items" in data:
        items = data["items"]
        if items in (None, []):
            return []
        if not isinstance(items, list):
            raise RuntimeError(f"{label} data.items 不是列表。")
        return items
    raise RuntimeError(f"无法解析{label}：期望列表或带 items 的对象，实际 {type(data).__name__}。")


def _event_id(item: dict, summary: str) -> str:
    raw = item.get("event_id")
    if raw is None or raw == "":
        return ""
    if type(raw) is not str:
        raise RuntimeError(f"日程「{summary}」的 event_id 必须是字符串。")
    return raw.strip()


@_synced
def delete_agenda_event(event_id: object) -> dict:
    target = _target_id(event_id, '日程')
    day = today_date()
    cached = _require_today(day)
    snap, _details = _remote_update([
        'calendar', 'events', 'delete', '--calendar-id', 'primary', '--event-id', target,
        '--need-notification', 'true', '--as', 'user'], day, 'agenda',
        _target_context(cached, 'agenda', 'event_id', target))
    return dict(ok=True, **snap)


def _target_id(value, label):
    if type(value) is not str or not value.strip():
        raise RuntimeError(f'没有{label} ID，不能删除。')
    if any(ch in value for ch in '\n\r'):
        raise RuntimeError(f'{label} ID 不能包含换行。')
    return value.strip()


def _require_today(day):
    cached = _read_today_file()
    if cached is None or cached['date'] != day:
        raise RuntimeError('还没有今天的看板。先打开今日页再删除。')
    return cached


def _target_context(cached, section, key, target):
    details = {key: target}
    row = next((row for row in cached[section]['items'] if isinstance(row, dict) and row.get(key) == target), {})
    details.update({name: row[name] for name in ('summary', 'start', 'end', 'due_at')
                    if isinstance(row.get(name), str) and row[name]})
    return details


def _remote_update(args, day, section, target, *, allow_no_cache=False):
    details = dict(target, remote_state='unconfirmed', cache_state='not_started',
                   change_state='unconfirmed', stage='remote')
    try:
        env = _require_lark_ok(_run_lark(args))
        details.update(remote_state='confirmed', change_state='committed', stage='query')
        data = env.get('data')
        if allow_no_cache and isinstance(data, dict):
            event = data.get('event') if isinstance(data.get('event'), dict) else data
            if isinstance(event.get('event_id'), str) and event['event_id'].strip():
                details['event_id'] = event['event_id'].strip()
        _assert_day(day)
        cached = _read_today_file()
        if allow_no_cache and (cached is None or cached['date'] != day):
            return None, details
        result = (_section(lambda: parse_agenda(_lark_data(['calendar', '+agenda'])))
                  if section == 'agenda' else _open_tasks_due_today())
        snap = _merge_sections(day, {section: result})
        details['cache_state'] = 'confirmed'
        if not result['ok']:
            details['snapshot'] = dict(ok=True, **snap)
            raise BoardChangeError(result['error'], stage='query', change_state='committed')
        return snap, details
    except Exception as exc:
        if isinstance(exc, BoardChangeError):
            details.update(exc.details)
        # A cache/day failure cannot erase the confirmed remote side effect.
        if details['remote_state'] == 'confirmed':
            if details['stage'] == 'day_change':
                details['change_state'] = 'committed'
            prefix = '飞书操作已成功，但后续处理未完整完成'
        else:
            prefix = '飞书修改结果未确认'
        identity = ' '.join(f'{key}={details[key]}' for key in ('event_id', 'guid', 'summary', 'start', 'end', 'due_at') if details.get(key))
        raise BoardChangeError(f'{prefix}（{details["stage"]}）：{exc}。{identity} 请先核对结果，不要直接重复提交。',
                               **details) from exc


_EVENT_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})$"
)


def _parse_event_time(value: object, label: str) -> datetime:
    if type(value) is not str or not _EVENT_TIME.match(value):
        raise RuntimeError(f"{label}必须是带时区的时间，例如 2026-10-06T15:00:00+08:00。")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RuntimeError(f"{label}无法解析：{value}") from exc
    if parsed.tzinfo is None:
        raise RuntimeError(f"{label}必须带时区。")
    return parsed


@_synced
def create_board_event(summary: object, start: object, end: object) -> dict:
    """Create once; report remote/query/cache stages without automatically retrying."""
    if type(summary) is not str or not summary.strip():
        raise RuntimeError('要填写日程标题。')
    if any(ch in summary for ch in '\n\r'):
        raise RuntimeError('标题不能换行。')
    start_at = _parse_event_time(start, '开始时间')
    end_at = _parse_event_time(end, '结束时间')
    if start_at >= end_at:
        raise RuntimeError('开始时间必须早于结束时间。')
    title = summary.strip()
    day = today_date()
    target = dict(summary=title, start=str(start), end=str(end))
    snap, details = _remote_update(['calendar', '+create', '--summary', title, '--start', str(start),
        '--end', str(end), '--calendar-id', 'primary', '--as', 'user'], day, 'agenda', target,
        allow_no_cache=True)
    on_today = start_at.astimezone(TZ).date().isoformat() == day
    result = dict(ok=True, **target, on_today=on_today,
        notice='' if on_today else '已创建。今天的看板只显示今天。',
        snapshot=dict(ok=True, **snap) if snap is not None else None)
    if details.get('event_id'):
        result['event_id'] = details['event_id']
    return result


@_synced
def delete_board_task(guid: object) -> dict:
    target = _target_id(guid, '任务')
    day = today_date()
    cached = _require_today(day)
    snap, _details = _remote_update(['task', 'tasks', 'delete', '--task-guid', target,
        '--as', 'user', '--yes'], day, 'tasks', _target_context(cached, 'tasks', 'guid', target))
    return dict(ok=True, **snap)


def _require_lark_ok(raw: str) -> dict:
    try:
        env = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'lark-cli 未返回 JSON。\n{raw}') from exc
    if not isinstance(env, dict) or env.get('ok') is not True:
        raise RuntimeError(f'lark-cli 返回失败信封。\n{raw}')
    return env


def _event_time(item: dict, which: str, *, with_date: bool = False) -> str:
    for key in (which, f"{which}_time"):
        val = item.get(key)
        if val is None:
            continue
        if isinstance(val, str) and val.strip():
            return _short_time(val, with_date=with_date)
        if isinstance(val, dict):
            if val.get("date"):
                return f"{val['date']} 全天"
            if val.get("timestamp") not in (None, ""):
                ts = int(val["timestamp"])
                fmt = "%Y-%m-%d %H:%M" if with_date else "%H:%M"
                return datetime.fromtimestamp(ts, TZ).strftime(fmt)
            raw = val.get("time") or val.get("datetime")
            if raw:
                return _short_time(str(raw), with_date=with_date)
    return ""


def _span_labels(item: dict, start: str, end: str) -> tuple[str, str]:
    """起止不在东八区同一天时，两边都带上月日，避免结束只剩钟点。"""
    start_at = _event_point(item, "start")
    end_at = _event_point(item, "end")
    if start_at is None or end_at is None or start_at.date() == end_at.date():
        return start, end
    return _month_day_clock(start_at), _month_day_clock(end_at)


def _event_point(item: dict, which: str) -> datetime | None:
    for key in (which, f"{which}_time"):
        val = item.get(key)
        if val is None:
            continue
        if isinstance(val, str) and val.strip():
            return _parse_clock(val)
        if isinstance(val, dict):
            if val.get("date"):
                return None
            if val.get("timestamp") not in (None, ""):
                return datetime.fromtimestamp(int(val["timestamp"]), TZ)
            raw = val.get("time") or val.get("datetime")
            if raw:
                return _parse_clock(str(raw))
    return None


def _parse_clock(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00")).astimezone(TZ)
    except ValueError:
        return None


def _month_day_clock(moment: datetime) -> str:
    local = moment.astimezone(TZ)
    return f"{local.month}月{local.day}日 {local.strftime('%H:%M')}"


def _short_time(raw: str, *, with_date: bool = False) -> str:
    text = raw.strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        fmt = "%Y-%m-%d %H:%M" if with_date else "%H:%M"
        return dt.astimezone(TZ).strftime(fmt)
    except ValueError:
        if "T" in text:
            return text.replace("T", " ")[:16] if with_date else text.split("T")[1][:5]
        return text


def bubble_today(*, refresh: bool) -> dict:
    """气泡「今日」壳：一块重点 + 最多 4 条其余。失败原文，不编造。"""
    snap = load_today_snapshot(refresh=refresh)
    now = datetime.now(TZ)
    errors: list[str] = []
    agenda = snap["agenda"]
    tasks = snap["tasks"]
    events: list[dict] = []
    todos: list[dict] = []
    if agenda["ok"]:
        events = list(agenda.get("items") or [])
    else:
        errors.append(str(agenda.get("error") or "读取日程失败。"))
    if tasks["ok"]:
        todos = list(tasks.get("items") or [])
    else:
        errors.append(str(tasks.get("error") or "读取待办失败。"))

    focus = None
    used = None
    overdue = [item for item in todos if _task_overdue(item.get("due_at") or "", now)]
    if overdue:
        item = overdue[0]
        focus = {"kind": "task", "title": item["summary"], "when": _when_label(item.get("due_at") or "", "已过期")}
        used = ("task", item["summary"], item.get("due_at") or "")
    else:
        nxt = _next_event(events, now)
        if nxt is not None:
            focus = {"kind": "event", "title": nxt["summary"], "when": nxt.get("start") or ""}
            used = ("event", nxt["summary"], nxt.get("start") or "")
        elif events:
            item = events[0]
            focus = {"kind": "event", "title": item["summary"], "when": item.get("start") or ""}
            used = ("event", item["summary"], item.get("start") or "")
        elif todos:
            item = todos[0]
            focus = {"kind": "task", "title": item["summary"], "when": _when_label(item.get("due_at") or "", "")}
            used = ("task", item["summary"], item.get("due_at") or "")
        else:
            if errors:
                focus = {"kind": "empty", "title": "今日安排读不到。", "when": ""}
            else:
                focus = {"kind": "empty", "title": "今天没有必须盯的事", "when": ""}

    rest: list[dict] = []
    for item in events:
        key = ("event", item["summary"], item.get("start") or "")
        if used == key:
            continue
        rest.append({"kind": "event", "title": item["summary"], "when": item.get("start") or ""})
    for item in todos:
        key = ("task", item["summary"], item.get("due_at") or "")
        if used == key:
            continue
        rest.append({"kind": "task", "title": item["summary"], "when": _when_label(item.get("due_at") or "", "")})
    more = max(0, len(rest) - 4)
    return {
        "ok": True,
        "date": snap["date"],
        "error": "\n".join(errors),
        "focus": focus,
        "rest": rest[:4],
        "more": more,
    }


def _when_label(raw: str, fallback: str) -> str:
    text = (raw or "").strip()
    if not text:
        return fallback
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.astimezone(TZ).strftime("%H:%M")
    except ValueError:
        return text


def _task_overdue(raw: str, now: datetime) -> bool:
    text = (raw or "").strip()
    if not text:
        return False
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt <= now
    except ValueError:
        return False


_DAY_CLOCK = re.compile(r"^(\d{1,2})月(\d{1,2})日 (\d{2}):(\d{2})$")


def _event_start_at(start: str, now: datetime) -> datetime | None:
    text = start.strip()
    if "全天" in text:
        return None
    hit = _DAY_CLOCK.fullmatch(text)
    if hit:
        month, day, hour, minute = (int(part) for part in hit.groups())
        year = now.year
        if month == 12 and now.month == 1:
            year -= 1
        elif month == 1 and now.month == 12:
            year += 1
        try:
            return now.replace(
                year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0
            )
        except ValueError:
            return None
    if len(text) < 5 or text[2] != ":":
        return None
    try:
        hour = int(text[:2])
        minute = int(text[3:5])
    except ValueError:
        return None
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _next_event(events: list[dict], now: datetime) -> dict | None:
    best = None
    best_delta = None
    for item in events:
        event_at = _event_start_at(str(item.get("start") or ""), now)
        if event_at is None:
            continue
        delta = (event_at - now).total_seconds()
        if delta < -60:
            continue
        if best is None or delta < best_delta:
            best = item
            best_delta = delta
    return best
