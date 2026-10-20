"""本机定时作业：固定动作，进程内触发。"""
from __future__ import annotations

from .paths import data_root

import json
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from .logutil import log
from .memory import TZ

CONFIG_NAME = "automation_jobs.json"
ACTIONS = ("retro_gen", "retro_write", "ai_news")
ACTION_ORDER = ACTIONS
ACTION_LABELS = {
    "retro_gen": "生成本周复盘",
    "retro_write": "覆盖写入飞书",
    "ai_news": "AI/Agent 资讯日报",
}
RESULTS = ("", "ok", "fail", "missed", "queued", "running")
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
_STORE_LOCK = threading.Lock()


def jobs_path() -> Path:
    return data_root() / CONFIG_NAME


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


def save_store(store: dict) -> None:
    path = jobs_path()
    payload = {
        "jobs": [_job_payload(job) for job in store["jobs"]],
        "last_alive_at": store["last_alive_at"],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temp = path.with_suffix(".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


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
        if cadence not in {"daily", "weekly"}:
            raise RuntimeError("周期只能是每天或每周。")
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
        save_store(store)
        return list_snapshot()


def delete_job(job_id: str) -> dict:
    target = (job_id or "").strip()
    if not target:
        raise RuntimeError("删除需要任务 id。")
    with _STORE_LOCK:
        store = load_store()
        keep = [item for item in store["jobs"] if item["id"] != target]
        if len(keep) == len(store["jobs"]):
            raise RuntimeError("没有这条定时任务。")
        store["jobs"] = keep
        save_store(store)
        return list_snapshot()


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


class AutomationScheduler:
    def __init__(self, host) -> None:
        self.host = host
        self._lock = threading.Lock()
        self._queued: dict[str, str] = {}
        self._running_action = ""
        self._worker: threading.Thread | None = None
        self._last_tick = 0.0
        self._booted = False
        self._news_active = {}
        self._stopping = threading.Event()

    def tick(self) -> None:
        if self._stopping.is_set():
            return
        now_m = time.monotonic()
        if self._last_tick and now_m - self._last_tick < TICK_SEC:
            return
        self._last_tick = now_m
        pending = []
        try:
            with self._lock:
                occupied = set(self._queued)
                if self._running_action:
                    occupied.add(self._running_action)
                if self._news_active:
                    occupied.add("ai_news")
            with _STORE_LOCK:
                store = load_store()
                now = _now()
                dirty = False
                if not self._booted:
                    dirty = _abandon_queued(store) or dirty
                    dirty = _mark_missed(store, now) or dirty
                    self._booted = True
                else:
                    dirty = _mark_missed(store, now) or dirty
                due_jobs = _due_jobs(store, now)
                for job in due_jobs:
                    key = _slot_key(_latest_slot(now, job) or now, job)
                    if job["action"] == "ai_news" and job["action"] in occupied:
                        continue  # 不消费这个时段；前一任务结束后再触发最新一次。
                    job["last_run_slot"] = key
                    dirty = True
                    if job["action"] in occupied:
                        continue
                    occupied.add(job["action"])
                    job["last_result"] = "queued"
                    job["last_error"] = ""
                    job["alert_seen"] = True
                    pending.append(job)
                dirty = _heartbeat(store, now) or dirty
                if dirty:
                    save_store(store)
            for job in pending:
                self._queue_action(job["action"], job["id"])
        except Exception as extra:
            log(f"automation tick: {extra}")
            return
        self._kick()

    def run_now(self, job_id: str) -> dict:
        target = (job_id or "").strip()
        if not target:
            raise RuntimeError("立刻执行需要任务 id。")
        with _STORE_LOCK:
            store = load_store()
            job = _find(store, target)
            action = job["action"]
        if not self._queue_action(action, target):
            raise RuntimeError("同类任务正在跑或已在排队，不会叠第二趟。")
        with _STORE_LOCK:
            store = load_store()
            job = _find(store, target)
            job["last_result"] = "queued"
            job["last_error"] = ""
            job["alert_seen"] = True
            save_store(store)
        self._kick()
        snap = self.snapshot()
        snap["message"] = "已加入执行队列。"
        return snap

    def snapshot(self) -> dict:
        with self._lock:
            queued = dict(self._queued)
        with _STORE_LOCK:
            return list_snapshot(queued=queued)

    def seen(self) -> dict:
        mark_alerts_seen()
        snap = self.snapshot()
        snap["alert"] = False
        return snap

    def drop_queued(self, job_id: str) -> None:
        with self._lock:
            drop = [key for key, value in self._queued.items() if value == job_id]
            for key in drop:
                del self._queued[key]

    def _queue_action(self, action: str, job_id: str) -> bool:
        with self._lock:
            if self._stopping.is_set() or action in self._queued or action == self._running_action or (action == "ai_news" and self._news_active):
                return False
            self._queued[action] = job_id
            return True

    def _kick(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            if not self._queued:
                return
            self._worker = threading.Thread(target=self._loop, daemon=True)
            self._worker.start()

    def _loop(self) -> None:
        deferred = False
        try:
            while True:
                if self._stopping.is_set():
                    return
                busy = self._host_busy() or deferred
                job_id = None
                action = ""
                with self._lock:
                    for key in ACTION_ORDER:
                        if busy and key != "ai_news":
                            continue
                        if key in self._queued:
                            action = key
                            job_id = self._queued.pop(key)
                            self._running_action = key
                            break
                if not job_id:
                    return
                try:
                    if self._execute(job_id) is False:
                        with self._lock:
                            if not self._stopping.is_set():
                                self._queued[action] = job_id
                        deferred = True
                        continue
                finally:
                    with self._lock:
                        self._running_action = ""
        finally:
            with self._lock:
                self._worker = None
            self._notify()
            if not deferred and self._queued and not self._stopping.is_set() and (not self._host_busy() or "ai_news" in self._queued):
                self._kick()

    def _host_busy(self) -> bool:
        host = self.host
        if getattr(host, "_agent_running", False):
            return True
        pet = getattr(host, "pet", None)
        if pet is not None and pet.busy:
            return True
        return False

    def _execute(self, job_id: str) -> bool | None:
        try:
            with _STORE_LOCK:
                store = load_store()
                job = _find(store, job_id)
                action = job["action"]
                doc = job["doc"]
        except Exception as extra:
            log(f"automation execute load: {extra}")
            return
        error = ""
        try:
            if action == "ai_news":
                self._start_news(job_id)
                return
            elif action in {"retro_gen", "retro_write"}:
                service = self.host.weekly_review
                result = (service.generate_week_review() if action == "retro_gen"
                          else service.write_scheduled_review(doc))
                if result.get('retryable_busy'):
                    return False
                if not result.get('ok'):
                    raise RuntimeError(result.get('error') or '周复盘执行失败。')
            else:
                raise RuntimeError(f"不认识的动作 {action}。")
        except Exception as extra:
            error = str(extra)
        with _STORE_LOCK:
            store = load_store()
            try:
                job = _find(store, job_id)
            except RuntimeError:
                return
            if error:
                job["last_result"] = "fail"
                job["last_error"] = error
                job["alert_seen"] = False
            else:
                job["last_result"] = "ok"
                job["last_error"] = ""
                job["alert_seen"] = True
            save_store(store)

    def _start_news(self, job_id):
        from .news_controller import start
        started = start(self.host, publish=True, job_id=job_id)
        with self._lock:
            self._news_active[job_id] = started["task_id"]
        self._news_result(job_id, "running", started["task_id"])
        threading.Thread(target=self._monitor_news, args=(job_id, started), daemon=True).start()

    def _news_result(self, job_id, result, task_id, error="", reset_slot=False):
        with _STORE_LOCK:
            store = load_store()
            try:
                job = _find(store, job_id)
            except RuntimeError:
                return
            job.update(last_result=result, last_task_id=task_id, last_error=error, alert_seen=result not in {"fail", "missed"})
            if reset_slot:
                job["last_run_slot"] = ""
            save_store(store)

    def _monitor_news(self, job_id, started):
        from .news import load_run
        from .news_controller import start
        from .tasks import TERMINAL
        deadline = None
        task_id = started["task_id"]
        finished = False
        try:
            for attempt in range(2):
                while True:
                    task = self.host.tasks.get(task_id, False)
                    if task["started"] and deadline is None:
                        deadline = time.monotonic() + max(0, 900 - (time.time() - task["started"]))
                    if task["state"] in TERMINAL or self._stopping.wait(.3):
                        break
                if task["state"] == "succeeded":
                    self._news_result(job_id, "ok", task_id)
                    finished = True
                    return
                run = load_run(started["run_id"])
                seconds = int((deadline or time.monotonic()) - time.monotonic())
                if attempt or self._stopping.is_set() or task["state"] == "cancelled" or not run.get("retryable") or seconds < 20:
                    if self._stopping.is_set():
                        self._news_result(job_id, "missed", task_id, "桌宠退出时资讯任务未完成，下次恢复最新一次并核实发布回执。", reset_slot=True)
                    else:
                        self._news_result(job_id, "fail", task_id, task["error"] or "资讯任务中断，已保留阶段回执。")
                    finished = True
                    return
                self.host.tasks.event(task_id, "status", "资讯任务遇到临时失败，将在剩余时间内重试一次，复用已完成阶段。")
                if self._stopping.wait(2):
                    return
                retried = start(self.host, publish=True, job_id=job_id, seconds=max(1, seconds-2), attempt=1)
                task_id = retried["task_id"]
                with self._lock:
                    self._news_active[job_id] = task_id
                self._news_result(job_id, "running", task_id)
        except Exception:
            self._news_result(job_id, "fail", task_id, "资讯恢复调度未完成，请检查任务记录和设置。")
            finished = True
        finally:
            if not finished and self._stopping.is_set():
                self._news_result(job_id, "missed", task_id, "桌宠退出时资讯任务未完成，下次恢复最新一次并核实发布回执。", reset_slot=True)
            with self._lock:
                self._news_active.pop(job_id, None)

    def stop(self):
        self._stopping.set()
        with self._lock:
            self._queued.clear()


    def _notify(self) -> None:
        host = self.host
        try:
            host.ui(lambda: host._eval_board("jobs_changed"))
        except Exception as extra:
            log(f"automation notify: {extra}")


def _job_payload(job: dict) -> dict:
    return {**{key: job[key] for key in JOB_KEYS}, "last_task_id": job.get("last_task_id", "")}


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
    if cadence not in {"daily", "weekly"}:
        raise RuntimeError(f"{path} 的 jobs[{index}].cadence 只能是 daily 或 weekly。")
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
    }


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
    elif not days:
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
    if job["cadence"] == "daily":
        return True
    return day.weekday() in job["weekdays"]


def _slot_key(day: datetime, job: dict) -> str:
    return f"{day:%Y-%m-%d}T{job['hour']:02d}:{job['minute']:02d}"


def _slot_dt(day: datetime, job: dict) -> datetime:
    return day.replace(hour=job["hour"], minute=job["minute"], second=0, microsecond=0)


def _schedule_text(job: dict) -> str:
    clock = f"{job['hour']:02d}:{job['minute']:02d}"
    if job["cadence"] == "daily":
        return f"每天 {clock}"
    days = "、".join("周" + WEEKDAY_NAMES[d] for d in job["weekdays"])
    return f"每{days} {clock}"


def _abandon_queued(store: dict) -> bool:
    dirty = False
    for job in store["jobs"]:
        if job["last_result"] in {"queued", "running"}:
            job["last_result"] = "missed"
            job["last_error"] = "进程退出时仍在排队，未执行。"
            job["alert_seen"] = False
            dirty = True
            if job["action"] == "ai_news":
                job["last_run_slot"] = ""  # 最新一次补跑使用发布回执恢复，不从头重复写入。
    return dirty


def _mark_missed(store: dict, now: datetime) -> bool:
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
        if not job["enabled"]:
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
