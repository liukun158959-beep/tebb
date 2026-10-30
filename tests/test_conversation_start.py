"""首条消息原子创建私聊；隔离存储、假队列，不运行模型。"""
import copy
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from desk_companion.collaboration import Collaboration, transaction
from tests.test_collaboration import MODEL, Queue


class ConversationStartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, DESK_COMPANION_DATA_DIR=self.temp.name)
        self.env.start(); self.addCleanup(self.env.stop)
        for name in ("require_active", "require_item"):
            mocked = patch("desk_companion.model_catalog." + name, return_value=MODEL)
            mocked.start(); self.addCleanup(mocked.stop)
        self.path = Path(self.temp.name) / "workspace.sqlite3"
        self.queues = dict(chat=Queue(), work=Queue())
        self.service = Collaboration(path=self.path, managers=self.queues, start=False)
        self.addCleanup(self.service.shutdown)

    def first(self, **changes):
        ident = "dm-" + uuid.uuid4().hex
        return dict(type="send", request_id=ident, conversation=ident,
                    new_chat=dict(agent_id="tebb", project_id=""),
                    text="一个独立的明确问题", mode="message", **changes)

    def test_first_message_and_run_commit_once_even_after_selection_and_restart(self):
        action = self.first()
        before = self.service.snapshot()["state"]
        state = self.service.action(action)["state"]
        self.assertEqual(len(state["conversations"]), len(before["conversations"]) + 1)
        self.assertEqual(len(state["messages"]), len(before["messages"]) + 2)
        self.assertEqual(len(state["runs"]), len(before["runs"]) + 1)
        self.assertEqual(state["selected"], action["conversation"])
        row = next(c for c in state["conversations"] if c["id"] == action["conversation"])
        self.assertEqual(row["title"], action["text"])
        self.assertEqual(row["members"], ["tebb"])
        self.service.action(dict(type="select", conversation="dm-writer", request_id=uuid.uuid4().hex))
        restored = Collaboration(path=self.path, managers=self.queues, start=False)
        self.addCleanup(restored.shutdown)
        replayed = restored.action(copy.deepcopy(action))["state"]
        self.assertEqual(replayed["selected"], "dm-writer")
        self.assertEqual(len(replayed["runs"]), len(state["runs"]))
        self.assertEqual(len(replayed["conversations"]), len(state["conversations"]))
        changed = {**action, "text": "另一条消息"}
        with self.assertRaisesRegex(ValueError, "去重"):
            restored.action(changed)
        with self.assertRaises(ValueError):
            restored.action({**action, "request_id": uuid.uuid4().hex})

    def test_rejected_first_send_leaves_no_empty_chat_or_receipt(self):
        for change in (
            {"text": ""}, {"mode": "invalid"}, {"model_id": "missing"},
            {"new_chat": {"agent_id": "missing", "project_id": ""}},
            {"new_chat": {"agent_id": "tebb", "project_id": "missing"}},
            {"new_chat": []}, {"conversation": "invalid"},
            {"run": "another-task"},
        ):
            with self.subTest(change=change):
                before = self.service.snapshot()["state"]
                action = {**self.first(), **change}
                if "model_id" in change:
                    with patch("desk_companion.model_catalog.require_item", side_effect=ValueError("模型不存在")):
                        with self.assertRaises(ValueError): self.service.action(action)
                else:
                    with self.assertRaises(ValueError): self.service.action(action)
                after = self.service.snapshot()["state"]
                for key in ("conversations", "messages", "runs", "revision"):
                    self.assertEqual(after[key], before[key])
                with transaction(self.path) as (_, db):
                    self.assertIsNone(db.execute("SELECT id FROM receipts WHERE id=?", (action["request_id"],)).fetchone())

    def test_project_is_explicit_and_browser_cannot_bypass_tool_permissions(self):
        project = self.service.action(dict(type="create_project", name="隔离项目", request_id=uuid.uuid4().hex))["state"]["projects"][0]
        action = self.first()
        action["new_chat"]["project_id"] = project["id"]
        with self.assertRaisesRegex(ValueError, "本地工具"):
            self.service.action(action, readonly=True)
        state = self.service.action(action)["state"]
        row = next(c for c in state["conversations"] if c["id"] == action["conversation"])
        self.assertEqual(row["project_id"], project["id"])
        self.assertEqual(state["runs"][-1]["conversation"], row["id"])
