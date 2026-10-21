"""Shared timezone and strict absolute calendar-time parsing."""
from datetime import datetime, timedelta, timezone
import re

TZ = timezone(timedelta(hours=8))


_EVENT_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})$"
)


def parse_event_time(value: object, label: str) -> datetime:
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
