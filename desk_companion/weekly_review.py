"""Weekly review generation and publication; no GUI or scheduler dependencies."""
from contextlib import nullcontext
from datetime import datetime
import os
import uuid
from urllib.parse import urlsplit

from . import board_data
from .model_text import require_text
from .task_budgets import budgeted_operation


SUMMARY_ASK = """【本周复盘】不要调用工具。只根据后面的本周材料写中文 Markdown，不要编造材料里没有的事。
结构、红线和能用的语法以【技能规程】为准，不要另起章节，不要把整篇包进代码块。
不要把会议、待办或对话写成产出。"""


class WeeklyReviewService:
    def __init__(self, chat_sessions, *, start_guard=nullcontext, open_document=None):
        self.sessions = chat_sessions
        self._start_guard = start_guard
        self._open_document = open_document or os.startfile

    def _run(self, action):
        conversation = self.sessions.conversation
        reserved = False
        details = dict(stage='admission', change_state='not_changed', steps={})
        try:
            with self._start_guard(), self.sessions.selected_session():
                if conversation.running:
                    raise RuntimeError('助手正在处理其他操作，完成或停止后再操作周复盘。')
                conversation.running = True
                reserved = True
            return action(details)
        except Exception as exc:
            if not reserved:
                # The scheduler must retain its queue on the final admission race.
                details['retryable_busy'] = True
            if isinstance(exc, board_data.BoardChangeError):
                details.update(exc.details)
                if details.get('remote_state') == 'confirmed':
                    details['change_state'] = 'committed'
            from .feishu_auth import DocumentCreationError
            if isinstance(exc, DocumentCreationError):
                details.update(stage='remote_result', document_id=exc.document_id,
                               remote_state='confirmed', change_state='committed')
                details['steps']['remote'] = 'confirmed'
            partial = getattr(exc, 'partial', None)
            if isinstance(partial, dict):
                message = partial.get('message')
                if isinstance(message, dict) and isinstance(message.get('content'), str):
                    details['summary'] = message['content']
            details['steps'].setdefault(details['stage'], 'failed')
            if details.get('remote_state') == 'confirmed':
                hint = '飞书写入已完成，请使用保留的目标或链接核对，不要重复发布。'
            elif details.get('remote_state') == 'unconfirmed':
                hint = '飞书写入结果未确认，请先核对远端，不要直接重发。'
            elif details['change_state'] == 'unconfirmed':
                hint = '本地保存结果未确认，请重新读取核对，不要直接重新生成。'
            elif details['change_state'] == 'committed':
                hint = '本地保存已完成，请重新读取核对，不要直接重新生成。'
            else:
                hint = '已有复盘未替换。'
            targets = ' '.join(f'{key}={details[key]}' for key in ('target', 'url', 'document_id')
                               if details.get(key))
            return dict(details, ok=False, error=f'周复盘未完整完成（{details["stage"]}）：{exc}。{hint} {targets}'.strip())
        finally:
            if reserved:
                with self.sessions.selected_session():
                    conversation.running = False

    @budgeted_operation('weekly_review','research')
    def generate_week_review(self):
        def run(details):
            conversation = self.sessions.conversation
            details['answer_complete'] = False
            details['stage'] = 'model_setup'
            if conversation.agent is None:
                raise RuntimeError(conversation.agent_error or '还没接上模型。打开看板「模型」页填写。')
            monday, now = board_data.week_range()
            day = now.date().isoformat()
            details.update(date=day, material_start=monday.isoformat(), material_end=now.isoformat())
            details['stage'] = 'snapshot'
            board_data.load_today_snapshot(refresh=False)
            details['stage'] = 'feishu_materials'
            feishu = board_data.fetch_week_feishu(monday, now)
            for section in ('agenda', 'tasks'):
                if not feishu[section].get('ok'):
                    raise RuntimeError(feishu[section].get('error') or '本周飞书材料读取失败。')
            from .github import format_week_review_text
            from .skill_catalog import read_skill
            details['stage'] = 'github_materials'
            github_text = format_week_review_text(monday, now)
            details['stage'] = 'skill'
            skill = read_skill('weekly-retro')
            prompt = (SUMMARY_ASK + '\n\n【技能规程】\n' + skill['body']
                      + '\n\n【本周材料】\n' + _materials(monday, now, feishu, github_text))
            llm = conversation.agent.llm
            try:
                llm.sampling = None
                llm.on_reasoning = None
                details['stage'] = 'model'
                result = llm.chat([
                    dict(role='system', content=conversation.state.persona
                         + '\n\n你现在只写本周复盘，不要调用工具。按用户消息里的技能规程写。'),
                    dict(role='user', content=prompt),
                ], tools=None)
                message = result.get('message') if isinstance(result, dict) else None
                if isinstance(message, dict) and isinstance(message.get('content'), str):
                    details['summary'] = message['content']
                details['steps']['model'] = 'returned'
                details['stage'] = 'usage'
                conversation.record_llm_usage(result.get('usage'), str(uuid.uuid4()))
                details['steps']['usage'] = 'confirmed'
                details['stage'] = 'validation'
                markdown = require_text(result)
                details.update(summary=markdown, answer_complete=True)
                details['steps']['validation'] = 'confirmed'
            finally:
                llm.sampling = None
                llm.on_reasoning = None
            details['stage'] = 'cache_save'
            details['summary_at'] = now.isoformat(timespec='seconds')
            details.update(change_state='unconfirmed', cache_state='unconfirmed')
            snap = board_data.save_today_fields(expected_date=day, summary=markdown,
                summary_at=details['summary_at'], doc_url='')
            details.update(change_state='committed', cache_state='confirmed')
            details['steps']['cache_save'] = 'confirmed'
            return dict(details, ok=True, **snap,
                        message='本周复盘已写在自动化任务「周复盘」和今日页，并已落盘。')
        return self._run(run)

    def write_today_summary_doc(self):
        return self._publish(default_title=True)

    def write_week_review_doc(self, payload):
        return self._publish(payload=payload)

    def write_scheduled_review(self, doc):
        return self._publish(payload=dict(doc=doc), scheduled=True)

    def _publish(self, *, payload=None, default_title=False, scheduled=False):
        def run(details):
            monday, now = board_data.week_range()
            details['stage'] = 'snapshot'
            snap = board_data.load_today_snapshot(refresh=False)
            details.update(date=now.date().isoformat(), summary=snap['summary'], summary_at=snap['summary_at'])
            details['stage'] = 'validation'
            markdown = _current_summary(snap, monday, now)
            if default_title:
                doc = ''
                title = f"{monday:%Y-%m-%d}～{now:%Y-%m-%d} 周复盘"
            else:
                if not isinstance(payload, dict):
                    raise RuntimeError('写入参数必须是对象。')
                doc = str(payload.get('doc') or '').strip()
                title = str(payload.get('title') or '').strip()
            if (not doc and not title) or (doc and title) or (scheduled and not doc):
                raise RuntimeError('请选择一篇文档覆盖，或只填写新建标题。')
            details.update(target=doc or title, publication='overwrite' if doc else 'create',
                           remote_state='not_started', cache_state='not_started')
            from .feishu_auth import create_markdown_doc
            from .feishu_docs import overwrite_markdown_doc
            details['stage'] = 'remote'
            # Exceptions and malformed envelopes cannot confirm a remote side effect.
            details.update(remote_state='unconfirmed', change_state='unconfirmed')
            result = overwrite_markdown_doc(doc, markdown) if doc else create_markdown_doc(title, markdown)
            if isinstance(result, dict):
                if isinstance(result.get('url'), str):
                    details['url'] = result['url'].strip()
                if isinstance(result.get('document_id'), str) and result['document_id'].strip():
                    details['document_id'] = result['document_id'].strip()
            if not isinstance(result, dict) or result.get('ok') is not True:
                raise RuntimeError('飞书未返回明确成功结果。')
            url = details.get('url', '')
            if not doc and not _valid_url(url):
                raise RuntimeError('飞书创建返回结果缺少有效链接。')
            details.update(remote_state='confirmed', change_state='committed')
            details['steps']['remote'] = 'confirmed'
            if not scheduled and _valid_url(url):
                details['stage'] = 'cache_save'
                details['cache_state'] = 'unconfirmed'
                snap = board_data.save_today_fields(expected_date=details['date'], expected_summary=snap,
                                                    doc_url=details['url'])
                details['cache_state'] = 'confirmed'
                details['steps']['cache_save'] = 'confirmed'
            if not scheduled and not doc:
                details['stage'] = 'open'
                self._open_document(details['url'])
                details['steps']['open'] = 'requested'
            message = ('复盘已覆盖写入所选飞书文档。' if doc
                       else '复盘已写到飞书文档，并请求系统打开链接。')
            return dict(snap, **details, ok=True, message=message)
        return self._run(run)


def _current_summary(snap, monday, now):
    markdown = snap.get('summary')
    if not isinstance(markdown, str) or not markdown.strip():
        raise RuntimeError('还没有本周复盘正文。先生成或配一条更早的生成。')
    try:
        when = datetime.fromisoformat(snap.get('summary_at') or '')
        if when.tzinfo is None:
            raise ValueError('缺少时区')
    except (ValueError, TypeError) as exc:
        raise RuntimeError('复盘没有有效生成时间，不能确认是本周。请重新生成。') from exc
    if not monday <= when <= now:
        raise RuntimeError('复盘不属于当前周或生成时间在未来，请先核对再生成。')
    return markdown.strip()


def _valid_url(url):
    try:
        parts = urlsplit(url)
        return parts.scheme in {'http', 'https'} and bool(parts.netloc)
    except ValueError:
        return False


def _materials(monday, now, feishu, github_text):
    parts = [f"区间：{monday:%Y-%m-%d}～{now:%Y-%m-%d %H:%M} 东八区", '',
             '## GitHub 产出与待完成', github_text.strip() or '无', '', '## 飞书本周日程']
    agenda_items = feishu['agenda'].get('items') or []
    if not agenda_items:
        parts.append('本周无日程。')
    for item in agenda_items:
        when = item.get('start') or ''
        if item.get('end'):
            when = f"{when} – {item['end']}"
        parts.append(f"- {when} {item.get('summary') or ''}".strip())
    parts.extend(['', '## 飞书未完成待办'])
    task_items = feishu['tasks'].get('items') or []
    if not task_items:
        parts.append('无未完成待办。')
    for item in task_items:
        parts.append(f"- {item.get('summary') or ''}（截止 {item.get('due_at') or '无截止'}）")
    return '\n'.join(parts)
