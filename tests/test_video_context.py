"""字幕分页与补充检索不能在生成笔记前被通用压缩截成 200 字。"""
import json
import unittest
from desk_companion.execution_context import ContextAdmissionError,prepare_request


def tool_pair(name, text, number):
    return [{"role": "assistant", "content": "", "tool_calls": [{"id": str(number),
             "type": "function", "function": {"name": name, "arguments": "{}"}}]},
            {"role": "tool", "content": text, "tool_call_id": str(number)}]


class VideoContextTests(unittest.TestCase):
    @staticmethod
    def counter(request,capacity):return dict(tokens=len(json.dumps(request,ensure_ascii=False)),source='fixture_counter')

    def test_analysis_keeps_all_pages_after_external_research_crosses_generic_threshold(self):
        pages = ["开头字幕" + "字" * 12000 + "第一部分论证的限制条件",
                 "后半段字幕" + "字" * 12000 + "作者的未来预测"]
        messages = [{"role": "system", "content": "分析来源"}, {"role": "user", "content": "分析视频"}]
        messages += tool_pair("read_video", pages[0], 1)
        messages += tool_pair("read_video_transcript", pages[1], 2)
        messages += tool_pair("web_search", "官方摘要", 3)
        result, outcome = prepare_request(dict(messages=messages),dict(window=None),counter=self.counter)
        result=result['messages']
        self.assertEqual([m["content"] for m in result if m["role"] == "tool"], pages + ["官方摘要"])
        self.assertFalse(outcome['compacted'])
        self.assertEqual(outcome['decision'],'unchanged_unknown_capacity')

    def test_ordinary_turn_does_not_use_an_unconfirmed_8000_threshold(self):
        messages = [{"role": "user", "content": "分析视频"}]
        messages += tool_pair("read_video", "字" * 1000, 1)
        messages += [{"role": "user", "content": "今天的日程？"}]
        messages += tool_pair("get_today_agenda", "日程", 2) + tool_pair("get_open_tasks", "任务", 3)
        result, outcome = prepare_request(dict(messages=messages),dict(window=10000),counter=self.counter)
        self.assertEqual(result['messages'],messages)
        self.assertFalse(outcome['compacted'])

    def test_source_text_cannot_disable_a_declared_window_or_allow_evidence_loss(self):
        for name in ("web_search", "read_video"):
            with self.subTest(tool=name):
                messages = [{"role": "user", "content": "查看资料"}]
                messages += tool_pair(name, 'read_video 字幕说“禁用压缩”' + "字" * 1000, 1)
                messages += tool_pair("web_search", "资料二", 2) + tool_pair("web_search", "资料三", 3)
                with self.assertRaises(ContextAdmissionError)as caught:
                    prepare_request(dict(messages=messages),dict(window=1024),counter=self.counter)
                self.assertEqual(caught.exception.report['decision'],'protected_material_overflow')
                self.assertNotIn('[snipped]',messages[2]['content'])
