"""在受监督任务进程中归档；授权来自本机开关，不来自字幕指令。"""
import json
import re


class VideoArchive:
    def __init__(self, request, emit):
        self.request, self.emit = request, emit
        self.pages = {}
        self.manual_save = False

    def record(self, name, output):
        if name == "save_video_summary":
            self.manual_save = True
        if name not in {"read_video", "read_video_transcript"}:
            return
        try:
            value = json.loads(output) if isinstance(output, str) else output
            page = value.get("transcript", value)
            sid = page["source_id"]
            self.pages.setdefault(sid, set()).update(range(page["offset"], page["offset"] + len(page["items"])))
        except (ValueError, TypeError, KeyError, AttributeError):
            pass

    def finish(self, answer):
        from .video import export_summary, get_source
        cfg = self.request.get("video_archive_config", {})
        if not cfg.get("auto_video_save") or not self.request.get("auto_video_archive", True) or self.manual_save or not answer.strip():
            return
        # Explicit per-turn opt-out always wins over the persistent app setting.
        if re.search(r"(?:不要|不用|无需|不需要|禁止|别).{0,12}(?:保存|写入|归档|生成.{0,4}文档)|(?:do not|don't|no).{0,12}(?:save|archive|export)",
                     self.request["text"], re.I):
            self.emit("status", "本次按要求跳过视频笔记自动保存。")
            return
        for sid, offsets in self.pages.items():
            value = get_source(self.request["session_id"], sid)
            if value.get("subtitle_status") != "available" or value.get("truncated") or len(offsets) != len(value.get("segments", [])) or not offsets:
                self.emit("status", "可用字幕尚未完整读取，本次未自动保存视频笔记。")
                continue
            from .task_budgets import current,BudgetExceeded
            try:
                if current():current().tool(False,'archive_video_notes')
            except BudgetExceeded:
                self.emit('status','任务预算已到，未开始自动保存；视频答案已保留，可先核对再手动保存。')
                continue
            self.emit("status", "视频笔记已生成，正在自动保存到飞书知识库。")
            self.emit("tool_start", {"tool": "archive_video_notes", "read_only": False})
            try:
                result = export_summary(self.request["session_id"], sid, answer, destination=cfg)
                self.emit("status", "视频笔记已保存：" + result["url"])
                for warning in result.get("presentation_warnings", []):
                    self.emit("status", warning)
                self.emit("tool_end", {"tool": "archive_video_notes", "status": "success", "read_only": False})
            except Exception:
                self.emit("status", "视频答案已保留；飞书保存未确认。请在视频来源区查看回执，核实后再保存。")
                self.emit("tool_end", {"tool": "archive_video_notes", "status": "error", "read_only": False})
