"""本机定时作业：固定动作，进程内触发。"""
from __future__ import annotations

from .paths import data_root

import json
import hashlib
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps
from contextlib import contextmanager

from .memory import TZ
from .resource_lock import ResourceLock

CONFIG_NAME = "automation_jobs.json"
ACTIONS = ("retro_gen", "retro_write", "ai_news", "tebb_task")
ACTION_ORDER = ACTIONS
ACTION_LABELS = {
    "tebb_task": "交给 tebb 执行目标",
    "retro_gen": "生成本周复盘",
    "retro_write": "覆盖写入飞书",
    "ai_news": "AI/Agent 资讯日报",
}
RESULTS = ("", "ok", "fail", "missed", "queued", "running", "needs_input")
JOB_KEYS = (
    "id",
    "name",
    "action",
    "enabled",
    "cadence",
    "weekdays",
    "hour",
    "minute",
    "doc",
    "created_at",
    "last_run_slot",
    "last_result",
    "last_error",
    "alert_seen",
)
WEEKDAY_NAMES = "一二三四五六日"
TICK_SEC = 15
HEARTBEAT_SEC = 60
MISS_GAP_SEC = HEARTBEAT_SEC * 2
_STORE_LOCK = ResourceLock('automation-store')


class AutomationChangeError(RuntimeError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


@contextmanager
def transaction():
    """Keep read/modify/save together; callers release this before external work."""
    with _STORE_LOCK:
        yield


def _stored(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        with _STORE_LOCK:
            return fn(*args, **kwargs)
    return run


def jobs_path() -> Path:
    return data_root() / CONFIG_NAME


@_stored
def load_store() -> dict:
    path = jobs_path()
    if not path.is_file():
        return {"jobs": [], "last_alive_at": ""}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as extra:
        raise RuntimeError(f"{path} 不是合法 JSON。改正或删掉该文件后重启桌宠。") from extra
    if not isinstance(raw, dict):
        raise RuntimeError(f"{path} 根节点必须是对象。改正或删掉该文件后重启桌宠。")
    if "jobs" not in raw or "last_alive_at" not in raw:
        raise RuntimeError(
            f"{path} 必须有 jobs 和 last_alive_at。补上这些键，或删掉该文件。"
        )
    jobs = raw["jobs"]
    alive = raw["last_alive_at"]
    if not isinstance(jobs, list):
        raise RuntimeError(f"{path} 的 jobs 必须是数组。")
    if type(alive) is not str:
        raise RuntimeError(f"{path} 的 last_alive_at 必须是字符串。")
    out = []
    names = []
    ids = []
    for index, item in enumerate(jobs):
        # 旧游戏作业不再加载或执行，其余作业继续正常运行。
        if isinstance(item, dict) and item.get("action") == "maa_daily":
            continue
        rec = _parse_job(item, path, index)
        if rec["id"] in ids:
            raise RuntimeError(f"{path} 有重复 id {rec['id']}。")
        if rec["name"] in names:
            raise RuntimeError(f"{path} 有重复名称 {rec['name']!r}。")
        ids.append(rec["id"])
        names.append(rec["name"])
        out.append(rec)
    return {"jobs": out, "last_alive_at": alive}


@_stored
def save_store(store: dict) -> None:
    path = jobs_path()
    payload = {
        "jobs": [_job_payload(job) for job in store["jobs"]],
        "last_alive_at": store["last_alive_at"],
    }
    from .video import atomic_write
    try:
        atomic_write(path, payload)
    except Exception as exc:
        raise AutomationChangeError(f'自动化记录保存结果未确认：{exc}。请重新读取核对，不要直接重复执行。',
                                    stage='store_save', change_state='unconfirmed') from exc


def _idle(job):
    if job['last_result'] in {'queued', 'running'}:
        raise RuntimeError('该作业正在排队、执行或收尾，完成或停止后再修改、启停或删除。')


def _saved_snapshot(job_id):
    try:
        return dict(list_snapshot(), job_id=job_id)
    except Exception as exc:
        raise AutomationChangeError(f'配置已保存，但列表读取失败：{exc}。请重新读取核对。',
            stage='snapshot', change_state='committed', job_id=job_id) from exc


def list_snapshot(*, queued: dict[str, str] | None = None) -> dict:
    store = load_store()
    waiting = queued or {}
    queued_ids = set(waiting.values())
    items = []
    alert = False
    for job in store["jobs"]:
        row = dict(job)
        row["label"] = ACTION_LABELS[job["action"]]
        row["schedule_text"] = _schedule_text(job)
        row["queued"] = job["id"] in queued_ids
        if (
            job["last_result"] in {"fail", "missed"}
            and not job["alert_seen"]
        ):
            alert = True
        items.append(row)
    return {"ok": True, "items": items, "alert": alert}


def upsert_job(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise RuntimeError("任务参数必须是对象。")
    with _STORE_LOCK:
        store = load_store()
        job_id = str(payload.get("id") or "").strip()
        existing = None
        if job_id:
            for item in store["jobs"]:
                if item["id"] == job_id:
                    existing = item
                    break
            if existing is None:
                raise RuntimeError("没有这条定时任务。")
            _idle(existing)
        operation_id=payload.get('operation_id','')
        operation_fingerprint=hashlib.sha256(json.dumps({k:v for k,v in payload.items() if k!='operation_id'},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if operation_id and not job_id:
            found=next((j for j in store['jobs'] if j.get('operation_id')==operation_id),None)
            if found:
                if found.get('operation_fingerprint')!=operation_fingerprint:raise RuntimeError('操作编号已用于其他自动化目标。')
                return _saved_snapshot(found['id'])
        name = str(payload.get("name") or "").strip()
        if not name:
            raise RuntimeError("任务名称不能空。")
        for item in store["jobs"]:
            if item["name"] == name and item is not existing:
                raise RuntimeError(f"已有同名任务 {name!r}。")
        action = str(payload.get("action") or "").strip()
        if action not in ACTIONS:
            raise RuntimeError("请选择允许的定时动作。")
        cadence = str(payload.get("cadence") or "").strip()
        if cadence not in {"once", "daily", "weekly"}:
            raise RuntimeError("周期只能是一次、每天或每周。")
        weekdays = _parse_weekdays(payload.get("weekdays"), cadence)
        hour = _as_hour_minute(payload.get("hour"), "小时", 0, 23)
        minute = _as_hour_minute(payload.get("minute"), "分钟", 0, 59)
        enabled = payload.get("enabled")
        if type(enabled) is not bool:
            raise RuntimeError("启用必须是 true/false。")
        doc = str(payload.get("doc") or "").strip()
        if action == "retro_write":
            if not doc:
                raise RuntimeError("定时写入飞书必须绑一篇文档的 url 或 token。")
        elif doc:
            raise RuntimeError("只有覆盖写入飞书才能填文档。")
        fields = _goal_fields(payload) if action=='tebb_task' else {}
        if cadence=='once':
            if action!='tebb_task':raise RuntimeError('一次性仅支持通用AI目标。')
            if datetime.fromisoformat(fields['date']+'T%02d:%02d'%(hour,minute)).replace(tzinfo=TZ)<=_now() and enabled:
                raise RuntimeError('一次性执行时间须晚于当前时间。')
        now = _now()
        if existing is None:
            rec = {
                "id": str(uuid.uuid4()),
                "name": name,
                "action": action,
                "enabled": enabled,
                "cadence": cadence,
                "weekdays": weekdays,
                "hour": hour,
                "minute": minute,
                "doc": doc,
                "created_at": now.isoformat(timespec="seconds"),
                "last_run_slot": "",
                "last_result": "",
                "last_error": "",
                "alert_seen": True,
            }
            store["jobs"].append(rec)
        else:
            existing["name"] = name
            existing["action"] = action
            existing["enabled"] = enabled
            existing["cadence"] = cadence
            existing["weekdays"] = weekdays
            existing["hour"] = hour
            existing["minute"] = minute
            existing["doc"] = doc
        if not store["last_alive_at"]:
            store["last_alive_at"] = now.isoformat(timespec="seconds")
        target=rec if existing is None else existing
        target.update(fields)
        if operation_id:target.update(operation_id=operation_id,operation_fingerprint=operation_fingerprint)
        job_id = target['id']
        try:
            save_store(store)
        except AutomationChangeError as exc:
            exc.details['job_id'] = job_id
            raise
        return _saved_snapshot(job_id)


def delete_job(job_id: str) -> dict:
    target = (job_id or "").strip()
    if not target:
        raise RuntimeError("删除需要任务 id。")
    with _STORE_LOCK:
        store = load_store()
        _idle(_find(store, target))
        keep = [item for item in store["jobs"] if item["id"] != target]
        if len(keep) == len(store["jobs"]):
            raise RuntimeError("没有这条定时任务。")
        store["jobs"] = keep
        try:
            save_store(store)
        except AutomationChangeError as exc:
            exc.details['job_id'] = target
            raise
        return _saved_snapshot(target)


def mark_alerts_seen() -> dict:
    with _STORE_LOCK:
        store = load_store()
        dirty = False
        for job in store["jobs"]:
            if job["last_result"] in {"fail", "missed"} and not job["alert_seen"]:
                job["alert_seen"] = True
                dirty = True
        if dirty:
            save_store(store)
        return list_snapshot()


def has_alert() -> bool:
    store = load_store()
    return any(
        job["last_result"] in {"fail", "missed"} and not job["alert_seen"]
        for job in store["jobs"]
    )


def _job_payload(job: dict) -> dict:
    return {**{key: job[key] for key in JOB_KEYS}, "last_task_id": job.get("last_task_id", ""),
            **{k:job[k] for k in ("goal","project_id","agent_id","date","operation_id","operation_fingerprint") if k in job},
            **({"execution": job["execution"]} if "execution" in job else {})}


def _parse_job(item, path: Path, index: int) -> dict:
    if not isinstance(item, dict):
        raise RuntimeError(f"{path} 的 jobs[{index}] 必须是对象。")
    missing = [key for key in JOB_KEYS if key not in item]
    if missing:
        raise RuntimeError(
            f"{path} 的 jobs[{index}] 缺少字段 {', '.join(missing)}。补上或删掉该文件。"
        )
    job_id = item["id"]
    name = item["name"]
    action = item["action"]
    enabled = item["enabled"]
    cadence = item["cadence"]
    hour = item["hour"]
    minute = item["minute"]
    doc = item["doc"]
    created_at = item["created_at"]
    last_run_slot = item["last_run_slot"]
    last_result = item["last_result"]
    last_error = item["last_error"]
    alert_seen = item["alert_seen"]
    if type(job_id) is not str or not job_id.strip():
        raise RuntimeError(f"{path} 的 jobs[{index}].id 必须是非空字符串。")
    if type(name) is not str or not name.strip():
        raise RuntimeError(f"{path} 的 jobs[{index}].name 必须是非空字符串。")
    if action not in ACTIONS:
        raise RuntimeError(f"{path} 的 jobs[{index}].action 不是允许的动作。")
    if type(enabled) is not bool:
        raise RuntimeError(f"{path} 的 jobs[{index}].enabled 必须是 true/false。")
    if cadence not in {"once", "daily", "weekly"}:
        raise RuntimeError(f"{path} 的 jobs[{index}].cadence 只能是 daily 或 weekly。")
    if cadence=='once' and action!='tebb_task':raise RuntimeError('一次性仅支持通用AI目标。')
    weekdays = _parse_weekdays(item["weekdays"], cadence, where=f"{path} 的 jobs[{index}]")
    if type(hour) is not int or hour < 0 or hour > 23:
        raise RuntimeError(f"{path} 的 jobs[{index}].hour 必须是 0 到 23 的整数。")
    if type(minute) is not int or minute < 0 or minute > 59:
        raise RuntimeError(f"{path} 的 jobs[{index}].minute 必须是 0 到 59 的整数。")
    if type(doc) is not str:
        raise RuntimeError(f"{path} 的 jobs[{index}].doc 必须是字符串。")
    if action == "retro_write":
        if not doc.strip():
            raise RuntimeError(f"{path} 的 jobs[{index}] 是写入飞书但 doc 为空。")
    elif doc:
        raise RuntimeError(f"{path} 的 jobs[{index}] 不是写入飞书，doc 必须是空字符串。")
    if type(created_at) is not str or not created_at.strip():
        raise RuntimeError(f"{path} 的 jobs[{index}].created_at 必须是非空字符串。")
    _parse_iso(created_at, f"{path} 的 jobs[{index}].created_at")
    if type(last_run_slot) is not str:
        raise RuntimeError(f"{path} 的 jobs[{index}].last_run_slot 必须是字符串。")
    if last_result not in RESULTS:
        raise RuntimeError(f"{path} 的 jobs[{index}].last_result 不合法。")
    if type(last_error) is not str:
        raise RuntimeError(f"{path} 的 jobs[{index}].last_error 必须是字符串。")
    if type(alert_seen) is not bool:
        raise RuntimeError(f"{path} 的 jobs[{index}].alert_seen 必须是 true/false。")
    return {
        "id": job_id.strip(),
        "name": name.strip(),
        "action": action,
        "enabled": enabled,
        "cadence": cadence,
        "weekdays": weekdays,
        "hour": hour,
        "minute": minute,
        "doc": doc.strip(),
        "created_at": created_at,
        "last_run_slot": last_run_slot,
        "last_result": last_result,
        "last_error": last_error,
        "alert_seen": alert_seen,
        "last_task_id": str(item.get("last_task_id", "")),
        **(_goal_fields(item) if action=="tebb_task" else {}),
        **({k:item.get(k,"") for k in ("operation_id","operation_fingerprint")} if item.get("operation_id") else {}),
        **({"execution": _parse_execution(item["execution"])} if "execution" in item else {}),
    }


def _goal_fields(payload):
    goal=payload.get('goal','')
    if not isinstance(goal,str) or not 1<=len(goal.strip())<=4000:raise RuntimeError('AI目标须为1至4000字。')
    result=dict(goal=goal.strip(),project_id=payload.get('project_id',''),agent_id=payload.get('agent_id','tebb'),date=payload.get('date',''))
    if any(not isinstance(v,str) for v in result.values()):raise RuntimeError('AI目标配置字段无效。')
    if not result['agent_id']:raise RuntimeError('请选择AI负责人。')
    if payload.get('cadence')=='once':
        from datetime import date
        if date.fromisoformat(result['date']).isoformat()!=result['date']:raise RuntimeError('一次性日期格式无效。')
    return result


def _parse_execution(value):
    if not isinstance(value, dict) or any(type(value.get(k)) is not str
            for k in ('id', 'action', 'doc', 'phase')):
        raise RuntimeError('自动化 execution 必须包含字符串 id/action/doc/phase。')
    if value['action'] not in ACTIONS or ('result' in value and not isinstance(value['result'], dict)):
        raise RuntimeError('自动化 execution 的动作或结果无效。')
    return value


def _parse_weekdays(raw, cadence: str, where: str = "任务") -> list[int]:
    if not isinstance(raw, list):
        raise RuntimeError(f"{where} 的 weekdays 必须是数组。")
    days = []
    for item in raw:
        if type(item) is bool:
            raise RuntimeError(f"{where} 的星期必须是 0 到 6 的整数。")
        if type(item) is float and item == int(item):
            item = int(item)
        if type(item) is not int or item < 0 or item > 6:
            raise RuntimeError(f"{where} 的星期必须是 0 到 6 的整数。")
        if item not in days:
            days.append(item)
    days.sort()
    if cadence == "daily":
        if days:
            raise RuntimeError(f"{where} 每天触发时 weekdays 必须是空数组。")
    elif cadence=="weekly" and not days:
        raise RuntimeError(f"{where} 每周触发必须勾选至少一个星期。")
    return days


def _as_hour_minute(value, name: str, lo: int, hi: int) -> int:
    if type(value) is bool:
        raise RuntimeError(f"{name}必须是 {lo} 到 {hi} 的整数。")
    if type(value) is float and value == int(value):
        value = int(value)
    if type(value) is not int or value < lo or value > hi:
        raise RuntimeError(f"{name}必须是 {lo} 到 {hi} 的整数。")
    return value


def _now() -> datetime:
    return datetime.now(TZ)


def _parse_iso(text: str, where: str = "时间") -> datetime:
    try:
        when = datetime.fromisoformat(text)
    except ValueError as extra:
        raise RuntimeError(f"{where} 不是合法 ISO 时间：{text}") from extra
    if when.tzinfo is None:
        when = when.replace(tzinfo=TZ)
    return when


def _find(store: dict, job_id: str) -> dict:
    for job in store["jobs"]:
        if job["id"] == job_id:
            return job
    raise RuntimeError("没有这条定时任务。")


def _runs_on(job: dict, day: datetime) -> bool:
    if job["cadence"]=="once":return day.date().isoformat()==job.get("date")
    if job["cadence"] == "daily":
        return True
    return day.weekday() in job["weekdays"]


def _slot_key(day: datetime, job: dict) -> str:
    return f"{day:%Y-%m-%d}T{job['hour']:02d}:{job['minute']:02d}"


def _slot_dt(day: datetime, job: dict) -> datetime:
    return day.replace(hour=job["hour"], minute=job["minute"], second=0, microsecond=0)


def _schedule_text(job: dict) -> str:
    clock = f"{job['hour']:02d}:{job['minute']:02d}"
    if job["cadence"]=="once":return job.get("date", "")+" "+clock+"（一次）"
    if job["cadence"] == "daily":
        return f"每天 {clock}"
    days = "、".join("周" + WEEKDAY_NAMES[d] for d in job["weekdays"])
    return f"每{days} {clock}"


def _abandon_queued(store: dict) -> bool:
    dirty = False
    for job in store["jobs"]:
        if job["last_result"] in {"queued", "running"} or job.get('execution', {}).get('phase') == 'unconfirmed':
            job["last_result"] = "missed"
            execution = job.get('execution') or {}
            not_started = execution.get('phase') == 'queued'
            job['last_error'] = ('进程退出时尚未开始，未执行。' if not_started else
                                 '上次执行结果未确认（含旧版无法判断的记录），请先核对，不要直接重发。')
            if execution:
                job['execution'] = dict(execution, phase='not_started' if not_started else 'unconfirmed')
            job["alert_seen"] = False
            dirty = True
            if job["action"] == "ai_news":
                job["last_run_slot"] = ""  # 最新一次补跑使用发布回执恢复，不从头重复写入。
            else:
                now = _now()
                if _slot_dt(now, job) <= now:
                    job['last_run_slot'] = _slot_key(now, job)
    return dirty


def _mark_missed(store: dict, now: datetime, *, active=()) -> bool:
    raw = store["last_alive_at"]
    if not raw:
        return False
    alive = _parse_iso(raw, "last_alive_at")
    if now - alive <= timedelta(seconds=MISS_GAP_SEC):
        return False
    dirty = False
    for job in store["jobs"]:
        if job["action"] == "ai_news":
            continue
        if not job["enabled"] or job["id"] in active:
            continue
        created = _parse_iso(job["created_at"], "created_at")
        latest = ""
        cursor = alive.date()
        end = now.date()
        while cursor <= end:
            day = datetime(cursor.year, cursor.month, cursor.day, tzinfo=TZ)
            if _runs_on(job, day):
                slot = _slot_dt(day, job)
                key = _slot_key(day, job)
                if alive < slot <= now and created < slot:
                    latest = key
            cursor = cursor + timedelta(days=1)
        if latest and job["last_run_slot"] != latest:
            job["last_run_slot"] = latest
            job["last_result"] = "missed"
            job["last_error"] = f"错过 {latest}（当时桌宠没在跑）。"
            job["alert_seen"] = False
            dirty = True
    return dirty


def _due_jobs(store: dict, now: datetime) -> list[dict]:
    due = []
    for job in store["jobs"]:
        if not job["enabled"]:
            continue
        if job["action"]=="tebb_task" and job.get("execution",{}).get("phase") in {'unconfirmed','needs_input'}:continue
        if job["action"] == "ai_news":
            latest = _latest_slot(now, job)
            if latest and job["last_run_slot"] != _slot_key(latest, job):
                due.append(job)
            continue
        if not _runs_on(job, now):
            continue
        slot = _slot_dt(now, job)
        if now < slot or now >= slot + timedelta(minutes=1):
            continue
        key = _slot_key(now, job)
        if job["last_run_slot"] == key:
            continue
        created = _parse_iso(job["created_at"], "created_at")
        if created >= slot:
            continue
        due.append(job)
    due.sort(key=lambda item: ACTION_ORDER.index(item["action"]))
    return due


def _latest_slot(now, job):
    if job["action"] != "ai_news":
        return None
    created = _parse_iso(job["created_at"], "created_at")
    for ago in range(8):
        day = now - timedelta(days=ago)
        slot = _slot_dt(day, job)
        if _runs_on(job, day) and created < slot <= now:
            return slot
    return None


def _heartbeat(store: dict, now: datetime) -> bool:
    if not store["jobs"] and not jobs_path().is_file():
        return False
    stamp = now.isoformat(timespec="seconds")
    prev = store["last_alive_at"]
    if prev:
        alive = _parse_iso(prev, "last_alive_at")
        if now - alive < timedelta(seconds=HEARTBEAT_SEC):
            return False
    store["last_alive_at"] = stamp
    return True
