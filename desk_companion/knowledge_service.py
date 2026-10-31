"""Knowledge management and legacy synchronous ask, independent of GUI/RPC."""
from contextlib import nullcontext
import uuid

from . import knowledge
from .material_sources import MaterialChangeError
from .model_text import require_text
from .task_budgets import budgeted_operation


class KnowledgeService:
    def __init__(self, chat_sessions, *, start_guard=nullcontext):
        self.sessions = chat_sessions
        self._start_guard = start_guard

    def load_knowledge(self):
        try:
            return knowledge.public_view()
        except Exception as exc:
            return dict(ok=False, error=str(exc))

    def _change(self, action):
        conversation = self.sessions.conversation
        reserved = False
        try:
            with self._start_guard(), self.sessions.selected_session():
                if conversation.running:
                    raise RuntimeError('助手正在处理其他操作，完成或停止后再操作知识库。')
                conversation.running = True
                reserved = True
            return action()
        except (knowledge.KnowledgeChangeError, MaterialChangeError) as exc:
            return dict(ok=False, error=str(exc), **exc.details)
        except Exception as exc:
            return dict(ok=False, change_state='not_changed', error=str(exc))
        finally:
            if reserved:
                with self.sessions.selected_session():
                    conversation.running = False

    def save_knowledge(self, payload):
        return self._change(lambda: knowledge.save_settings(payload))

    def download_knowledge(self, repo):
        return self._change(lambda: knowledge.download_model(repo))

    def delete_model(self, repo):
        return self._change(lambda: knowledge.delete_model(repo))

    def add_knowledge(self, doc_id, label, source=None):
        return self._change(lambda: knowledge.add_doc(doc_id, label, source))

    def delete_knowledge(self, doc_id):
        return self._change(lambda: knowledge.delete_doc(doc_id))

    def rebuild_knowledge(self):
        return self._change(knowledge.rebuild)

    def material_operation(self, payload):
        from .material_service import operation
        if isinstance(payload, dict) and payload.get('action', 'list') in {'list', 'cancel', 'read_version', 'retrieve'}:
            try:
                return operation(payload)
            except (knowledge.KnowledgeChangeError, MaterialChangeError) as exc:
                return dict(ok=False, error=str(exc), **exc.details)
            except Exception as exc:
                return dict(ok=False, error=str(exc))
        return self._change(lambda: operation(payload))

    @budgeted_operation('knowledge')
    def ask_knowledge(self, text):
        details = dict(stage='admission', change_state='not_changed', answer_complete=False)

        def run():
            conversation = self.sessions.conversation
            llm = None
            try:
                details['stage'] = 'model_setup'
                conversation.engage_model()
                llm = conversation.agent.llm
                llm.sampling = None
                llm.on_reasoning = None

                def complete(system, user):
                    details['stage'] = 'model'
                    result = llm.chat([dict(role='system', content=system),
                                       dict(role='user', content=user)], tools=None)
                    message = result.get('message') or {}
                    if isinstance(message, dict) and isinstance(message.get('content'), str):
                        details['answer'] = message['content']
                    details['stage'] = 'usage'
                    conversation.record_llm_usage(result.get('usage'), str(uuid.uuid4()))
                    details['stage'] = 'validation'
                    answer = require_text(result)
                    details['answer_complete'] = True
                    return answer

                details['stage'] = 'retrieval'
                trace = knowledge.ask(text, complete)
                return dict(ok=True, trace=trace)
            except Exception as exc:
                partial = getattr(exc, 'partial', None)
                if isinstance(partial, dict):
                    message = partial.get('message')
                    if isinstance(message, dict) and isinstance(message.get('content'), str):
                        details['answer'] = message['content']
                error = f'知识库试问未完整完成（{details["stage"]}）：{exc}'
                if details.get('answer'):
                    error += '\n\n已收到的正文（本次试问未成功）：\n' + details['answer']
                return dict(details, ok=False, error=error)
            finally:
                if llm is not None:
                    llm.sampling = None
                    llm.on_reasoning = None

        result = self._change(run)
        return result if result.get('ok') else dict(details, **result)
