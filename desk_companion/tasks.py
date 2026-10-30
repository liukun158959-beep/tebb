"""持久任务队列：会话内顺序执行，会话间独立进程并行。"""
from __future__ import annotations
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from .paths import data_root
from .task_process import ProcessTree

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}
DEFAULTS = {"parallel": 2, "call_timeout": 90, "task_timeout": 300, "pet_progress": True}


def submission_task_id(submission_key):
    """Stable identity for callers reconciling an acknowledged-or-unknown submission."""
    return uuid.uuid5(uuid.NAMESPACE_URL, 'tebb-task:' + submission_key).hex


class TaskManager:
    def __init__(self, host=None, path=None, command=None, prewarm=0):
        self.path = Path(path or data_root() / "memory" / "tasks.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.command = command or [sys.executable, "-u", "-m", "desk_companion.task_worker"]
        self.cv = threading.Condition(threading.RLock())
        self.active = {}
        self._session_reservations = set()
        self.listeners = {}
        self.stopped = False
        self.settings_path = self.path.parent / "task_settings.json"
        self.settings = {**DEFAULTS, **(json.loads(self.settings_path.read_text("utf-8")) if self.settings_path.exists() else {})}
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, session TEXT, channel TEXT, "
                       "text TEXT, request TEXT, state TEXT, answer TEXT DEFAULT '', error TEXT DEFAULT '', "
                       "created REAL, started REAL DEFAULT 0, ended REAL DEFAULT 0, source TEXT DEFAULT '{}')")
            db.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT, kind TEXT, data TEXT, ts REAL)")
            db.execute("UPDATE tasks SET state='interrupted', ended=?, error='桌宠退出时任务中断，已完成的工具不会自动重做。' "
                       "WHERE state IN ('queued','running','cancelling')", (time.time(),))
            db.commit()
        self.scheduler = threading.Thread(target=self._schedule, daemon=True)
        self.standby=None
        if prewarm:
            from .task_warmup import WorkerStandby
            self.standby=WorkerStandby(lambda:self._spawn(standby=True),min(2,prewarm))
        self.scheduler.start()

    def _spawn(self, standby=False):
        return subprocess.Popen(self.command+(['--standby'] if standby else []), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True,encoding='utf-8',errors='replace',cwd=str(Path(__file__).resolve().parents[1]),
            env={**os.environ,'PYTHONIOENCODING':'utf-8'},creationflags=0x08000000 if os.name=='nt' else 0)

    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return closing(db)

    def configure(self, parallel, call_timeout, task_timeout, pet_progress):
        if type(parallel) is not int or not 1 <= parallel <= 4:
            raise ValueError("并行数须为 1～4。")
        if type(call_timeout) is not int or not 10 <= call_timeout <= 300:
            raise ValueError("单次调用时限须为 10～300 秒。")
        if type(task_timeout) is not int or not call_timeout <= task_timeout <= 1800:
            raise ValueError("总时限须大于单次调用时限，且不超过 1800 秒。")
        if type(pet_progress) is not bool:
            raise ValueError("桌宠进度反馈须为开关。")
        with self.cv:
            self.settings = dict(parallel=parallel, call_timeout=call_timeout, task_timeout=task_timeout, pet_progress=pet_progress)
            self.settings_path.write_text(json.dumps(self.settings), "utf-8")
            self.cv.notify_all()
        return {"ok": True, "settings": self.settings}

    def submit(self, text, session, channel="desktop", source=None, callback=None, task_limits=None, submission_key="", **request):
        if not isinstance(text, str) or not text.strip() or len(text) > 200000:
            raise ValueError("请填写有效问题。")
        if not isinstance(session, str) or not session:
            raise ValueError("缺少会话。")
        if not isinstance(submission_key, str) or len(submission_key) > 200:
            raise ValueError("任务去重编号无效。")
        task_id = submission_task_id(submission_key) if submission_key else uuid.uuid4().hex
        with self.cv:
            if self.stopped:
                raise RuntimeError("桌宠正在退出。")
            if submission_key:
                with self.db() as db:
                    existing = db.execute("SELECT text,session,channel FROM tasks WHERE id=?", (task_id,)).fetchone()
                if existing:
                    if tuple(existing) != (text, session, channel):
                        raise ValueError("任务去重编号已用于其他任务。")
                    return task_id
            if session in self._session_reservations:
                raise RuntimeError('当前会话正在整理上下文，完成后再提交任务。')
            limits = {**self.settings, **(task_limits or {})}
            from .video_analysis import initial_video_url
            video_url = request.get("video_url") or (initial_video_url(text) if not request.get("resume_note")
                and not request.get("workflow") and not request.get("chips") and not request.get("knowledge") else None)
            if video_url:
                request.update(workflow="video_note", video_url=video_url)
                request.setdefault("video_focus", text)
                limits["task_timeout"] = max(limits["task_timeout"], 900)
                source = {**(source or {}), "workflow": "video", "video_url": video_url}
            if not 0 < limits["call_timeout"] <= limits["task_timeout"] <= 1800:
                raise ValueError("任务时限不正确。")
            request.update(text=text, session_id=session, limits=limits)
            request.setdefault("sampling", {"reasoning_effort": "low", "temperature": .5, "top_p": 1})
            from .wiki_connection import load_settings as wiki_settings
            request.setdefault("video_archive_config", wiki_settings())
            with self.db() as db:
                db.execute("INSERT INTO tasks(id,session,channel,text,request,state,created,source) VALUES(?,?,?,?,?,'queued',?,?)",
                           (task_id, session, channel, text, json.dumps(request), time.time(), json.dumps(source or {})))
                db.commit()
            if callback:
                self.listeners[task_id] = callback
            self.event(task_id, "status", "任务已登记，正在等待执行。")
            self.cv.notify_all()
        return task_id

    def get(self, task_id, events=True):
        with self.db() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError("任务不存在。")
            value = dict(row)
            value.pop("request")
            value["source"] = json.loads(value["source"])
            if events:
                value["events"] = [{**dict(e), "data": json.loads(e["data"])} for e in db.execute(
                    "SELECT seq,kind,data,ts FROM events WHERE task=? ORDER BY seq", (task_id,))]
        value["elapsed"] = round((value["ended"] or time.time()) - value["started"], 1) if value["started"] else 0
        return value

    @contextmanager
    def session_mutation_guard(self, session):
        """Protect selection/clearing through the final worker write and against new submissions."""
        with self.cv:
            if self.stopped:
                raise RuntimeError('桌宠正在退出，暂时不能修改会话。')
            if session in self._session_reservations:
                raise RuntimeError('当前会话正在整理上下文，完成后再修改会话。')
            with self.db() as db:
                pending = db.execute("SELECT 1 FROM tasks WHERE session=? AND state IN ('queued','running','cancelling') LIMIT 1",
                                     (session,)).fetchone()
            if pending or any(control['session'] == session for control in self.active.values()):
                raise RuntimeError('当前会话任务尚未结束或仍在收尾，等待完成或停止后再修改会话。')
            yield

    @contextmanager
    def reserve_session(self, session):
        """Reserve an idle session without holding the queue lock during model IO."""
        with self.session_mutation_guard(session):
            self._session_reservations.add(session)
        try:
            yield
        finally:
            with self.cv:
                self._session_reservations.remove(session)
                self.cv.notify_all()

    def list(self, channel="", session=""):
        with self.db() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM tasks WHERE (?='' OR channel=?) AND (?='' OR session=?) "
                                          "ORDER BY created DESC LIMIT 100", (channel, channel, session, session))]
        return {"ok": True, "settings": dict(self.settings), "items": [self.get(i, False) for i in ids]}

    def update(self, task_id, **values):
        with self.db() as db:
            db.execute("UPDATE tasks SET " + ",".join(f"{k}=?" for k in values) + " WHERE id=?", (*values.values(), task_id))
            db.commit()

    def update_source(self, task_id, **values):
        with self.cv:
            with self.db() as db:
                row = db.execute("SELECT source FROM tasks WHERE id=?", (task_id,)).fetchone()
                source = {**json.loads(row[0]), **values}
                db.execute("UPDATE tasks SET source=? WHERE id=?", (json.dumps(source), task_id))
                db.commit()
        return source

    def event(self, task_id, kind, data, *, replace_answer=False):
        # 思考仅流到当前窗口，不保存进监控台，不转发到飞书。
        if kind != "think":
            with self.db() as db:
                db.execute("INSERT INTO events(task,kind,data,ts) VALUES(?,?,?,?)", (task_id, kind, json.dumps(data), time.time()))
                if kind == "token":
                    query = "UPDATE tasks SET answer=? WHERE id=?" if replace_answer else "UPDATE tasks SET answer=answer||? WHERE id=?"
                    db.execute(query, (str(data), task_id))
                db.commit()
        callback = self.listeners.get(task_id)
        if callback:
            try:
                callback(kind, data)
            except Exception:
                pass  # 窗口关闭不会取消后台任务。
        with self.cv:
            self.cv.notify_all()

    def wait(self, task_id):
        with self.cv:
            while True:
                row = self.get(task_id)
                if row["state"] in TERMINAL:
                    return row
                self.cv.wait(.3)

    def cancel(self, task_id):
        with self.cv:
            row = self.get(task_id)
            if row["state"] in TERMINAL:
                return {"ok": True, "task": row}
            running = self.active.get(task_id)
            if running:
                running["cancel"].set()
                self.update(task_id, state="cancelling")
            else:
                self.finish(task_id, "cancelled", "已从队列移除。")
            self.cv.notify_all()
        return {"ok": True, "task": self.get(task_id)}

    def finish(self, task_id, state, error="", answer=None):
        values = dict(state=state, error=error, ended=time.time())
        if answer is not None:
            values["answer"] = answer
        self.update(task_id, **values)
        row = self.get(task_id, False)
        self.event(task_id, "done" if state == "succeeded" else "error", row["answer"] if state == "succeeded" else error)
        self.listeners.pop(task_id, None)

    def _schedule(self):
        while True:
            with self.cv:
                if self.stopped:
                    return
                with self.db() as db:
                    queue = [dict(r) for r in db.execute("SELECT * FROM tasks WHERE state='queued' ORDER BY created,rowid")]
                busy = {v["session"] for v in self.active.values()} | self._session_reservations
                for row in queue:
                    if len(self.active) >= self.settings["parallel"]:
                        break
                    if row["session"] in busy:
                        continue
                    control = {"session": row["session"], "cancel": threading.Event()}
                    self.active[row["id"]] = control
                    busy.add(row["session"])
                    self.update(row["id"], state="running", started=time.time())
                    threading.Thread(target=self._execute, args=(row, control), daemon=True).start()
                self.cv.wait(.2)

    def _execute(self, row, control):
        task_id = row["id"]
        request = json.loads(row["request"])
        request.update(task_id=task_id, channel=row["channel"])
        tree = process = None
        phase = {"deadline": None, "hard_deadline": None, "kind": None, "waiting_note": None}
        ended = threading.Event()
        outcome = {}
        try:
            self.event(task_id, "status", "正在整理上下文，准备调用模型。")
            spawn_started = time.monotonic()
            spare=self.standby.take() if self.standby else None
            if spare:process,tree=spare
            else:process=self._spawn();tree=ProcessTree(process)
            self.event(task_id,'worker_pool',dict(prewarmed=bool(spare),
                spawn_duration_ms=int((time.monotonic()-spawn_started)*1000)))
            # worker 初始化前挂入 Job；从此包括工具子进程在内都能及时停止。
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()

            def read():
                try:
                    for line in process.stdout:
                        event = json.loads(line)
                        kind, data = event["kind"], event.get("data")
                        if kind == "result":
                            outcome["answer"] = str(data)
                            self.update(task_id, answer=outcome["answer"])
                        elif kind == "answer_reset":
                            # Keep the last received draft while tools/revisions wait.
                            # The first new token atomically replaces it, never
                            # publishing a blank answer between the two versions.
                            outcome['replace_pending'] = True
                            self.event(task_id, kind, data)
                        elif kind == "failure":
                            outcome["error"] = data.get("message", "任务失败") if isinstance(data, dict) else str(data)
                            outcome["timed_out"] = isinstance(data, dict) and "Timeout" in data.get("type", "")
                            self.event(task_id, kind, data)
                        else:
                            if kind in ("llm_start", "tool_start"):
                                wait = request['limits']['call_timeout']
                                if kind == 'tool_start' and isinstance(data,dict):
                                    command_wait = data.get('phase_timeout')
                                    # Trusted workers may declare a bounded tool wait;
                                    # the task's immutable total deadline still wins.
                                    if type(command_wait) in (int,float) and 1 <= command_wait <= 360:
                                        wait = max(wait,command_wait)
                                phase["deadline"] = time.monotonic() + wait
                                phase["kind"] = kind
                                phase["hard_deadline"] = time.monotonic() + max(300, request["limits"]["call_timeout"]) if kind == "llm_start" else None
                                phase["waiting_note"] = time.monotonic() + 30
                            elif kind in ("llm_end", "tool_end"):
                                phase["deadline"] = None
                                phase["hard_deadline"] = None
                                phase["kind"] = None
                                phase["waiting_note"] = None
                            elif kind in ("token", "think", "llm_progress") and data and phase["kind"] == "llm_start":
                                phase["deadline"] = time.monotonic() + request["limits"]["call_timeout"]
                                phase["waiting_note"] = time.monotonic() + 30
                            if kind == "llm_progress":
                                continue
                            replace = kind == 'token' and bool(data) and outcome.pop('replace_pending', False)
                            self.event(task_id, kind, data, replace_answer=replace)
                except Exception:
                    outcome["error"] = "任务通信中断，工具可能已经执行，请查看时间线。"
                finally:
                    ended.set()

            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            deadline = time.monotonic() + request["limits"]["task_timeout"]
            reason = ""
            while not ended.wait(.05):
                if control["cancel"].is_set():
                    reason = "cancelled"
                    break
                if time.monotonic() >= deadline or (phase["deadline"] and time.monotonic() >= phase["deadline"]) or (phase["hard_deadline"] and time.monotonic() >= phase["hard_deadline"]):
                    reason = "timed_out"
                    break
                if phase["waiting_note"] and time.monotonic() >= phase["waiting_note"]:
                    self.event(task_id, "status", "当前调用仍在等待返回；此提示不代表超时或失败。")
                    phase["waiting_note"] = None
            if reason:
                if reason == 'timed_out':
                    self.event(task_id, 'timeout_phase', dict(
                        stage=phase['kind'] or 'outside_call',
                        scope='task' if time.monotonic() >= deadline else 'call'))
                control["cancel"].set()
                tree.close()
                reader.join(2)
                if reason == 'cancelled':
                    message = '任务已停止。'
                elif time.monotonic() >= deadline:
                    message = '任务总时限已到，已停止等待。'
                elif phase['kind'] == 'llm_start':
                    message = '模型接口等待达到时限，已停止等待。可在调用调试中查看连接与响应耗时。'
                elif phase['kind'] == 'tool_start':
                    message = '工具执行等待达到时限，已停止等待。请查看工具回执。'
                else:
                    message = '调用达到时限，已停止等待。'
                if reason == "timed_out" and "answer" in outcome:
                    self.finish(task_id, "succeeded", "笔记已完成；自动归档达到时限，请核实飞书保存回执。", answer=outcome["answer"])
                else:
                    self.finish(task_id, reason, message + "已保留部分答案和工具记录；继续前请确认已执行的操作。")
                    self._save_partial(row, reason)
            elif "answer" in outcome:
                self.finish(task_id, "succeeded", answer=outcome["answer"])
            else:
                self.finish(task_id, "timed_out" if outcome.get("timed_out") else "failed", outcome.get("error", "任务进程意外退出，请检查模型配置。"))
                self._save_partial(row, "failed")
        except Exception:
            self.finish(task_id, "failed", "无法启动独立任务，请检查后端日志或重启桌宠。")
        finally:
            if row["channel"] != "collaboration":
                from .terminal import manager
                manager().cancel_task(task_id)
            if tree:
                tree.close()
            if process:
                for pipe in (process.stdin, process.stdout):
                    if pipe:
                        pipe.close()
            with self.cv:
                self.active.pop(task_id, None)
                self.cv.notify_all()

    def _save_partial(self, row, reason):
        if row["channel"] == "collaboration":
            return  # Collaboration owns its private history and partial results.
        from .memory import append_chat
        value = self.get(row["id"], False)
        if value["answer"]:
            append_chat("pet", value["answer"] + "\n\n[任务中断：" + reason + "，部分内容]", row["session"])

    def resume(self, task_id, text, callback=None, send_back=False):
        previous = self.get(task_id)
        if previous["state"] not in TERMINAL:
            raise ValueError("请先停止当前任务或等待完成。")
        steps = [e["data"] for e in previous["events"] if e["kind"] in ("tool_start", "tool_end")]
        note = "\n\n【继续任务】前次工具记录（开始但未结束代表结果未知，须查询确认，不可盲目重做）：\n" + json.dumps(steps, ensure_ascii=False)
        note += "\n前次部分答案：\n" + previous["answer"][-10000:] + "\n避免重复执行已完成的写入操作。"
        with self.db() as db:
            request = json.loads(db.execute("SELECT request FROM tasks WHERE id=?", (task_id,)).fetchone()[0])
        for key in ("text", "session_id", "limits"):
            request.pop(key, None)
        # Successful-answer follow-ups do not create another full note automatically.
        if previous["state"] == "succeeded":
            request["auto_video_archive"] = False
            if request.get("workflow") == "video_note":
                for key in ("workflow", "video_url", "video_focus"):
                    request.pop(key, None)
        elif not request.get("workflow") and not request.get("chips") and not request.get("knowledge"):
            from .video_analysis import initial_video_url
            original_url = initial_video_url(previous["text"])
            if original_url and all(e.get("read_only", True) for e in steps if isinstance(e, dict)):
                request.update(workflow="video_note", video_url=original_url, video_focus=previous["text"])
        completed = {e["data"].get("fingerprint") for e in previous["events"] if e["kind"] in {"tool_start", "tool_end"}
                     and not e["data"].get("read_only", True)}
        request["completed_writes"] = list(set(request.get("completed_writes", [])) | (completed - {None}))
        source = {k: v for k, v in previous["source"].items() if k not in {"card_id", "reply_id", "card_seq", "sent", "delivery_state"}}
        return self.submit(text, previous["session"], previous["channel"], source={**source, "parent_task": task_id,
                           "send_back": send_back}, callback=callback, resume_note=note, **{k:v for k,v in request.items() if k != "resume_note"})

    def shutdown(self):
        with self.cv:
            self.stopped = True
            for control in self.active.values():
                control["cancel"].set()
            with self.db() as db:
                db.execute("UPDATE tasks SET state='interrupted', ended=? WHERE state='queued'", (time.time(),))
                db.commit()
            self.cv.notify_all()
            deadline = time.monotonic() + 4
            while self.active and time.monotonic() < deadline:
                self.cv.wait(.05)
        self.scheduler.join(1)
        if self.standby:self.standby.shutdown()
