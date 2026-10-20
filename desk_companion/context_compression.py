"""Manual compression lifecycle, without GUI dependencies or locks around model IO."""
from __future__ import annotations

from contextlib import ExitStack, nullcontext

from .context_pack import save_pack
from .resource_lock import WRITES


class ContextCompressionService:
    def __init__(self, chat_sessions, *, start_guard=nullcontext):
        self.sessions = chat_sessions
        self._start_guard = start_guard

    def compress_context(self):
        phase = 'not_changed'

        def save(sid, summary, covered):
            nonlocal phase
            with WRITES:
                phase = 'unconfirmed'
                result = save_pack(sid, summary, covered)
                phase = 'committed'
                return result

        try:
            with ExitStack() as lifetime:
                # Serialize only admission/model binding with existing host operations.
                with self._start_guard():
                    lifetime.enter_context(self.sessions.reserve_operation())
                    conversation = self.sessions.conversation
                    conversation.engage_model()
                conversation.prepare_context('', manual=True, save_pack=save)
                snapshot = self.sessions.load_memory()
                if not snapshot['ok']:
                    raise RuntimeError(snapshot['error'])
                return snapshot
        except Exception as exc:
            prefix = {'not_changed': '压缩未完成，未替换摘要',
                      'unconfirmed': '摘要保存结果未确认，请重新读取核对',
                      'committed': '摘要已保存，但后续读取或清理失败，请重新读取核对'}[phase]
            return dict(ok=False, change_state=phase, error=f'{prefix}：{exc}')
