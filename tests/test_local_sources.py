"""本地资料读取和真实 Agent 输入链路；只读临时文件，不访问个人资料或模型接口。"""
import json
import os
import tempfile
import unittest
import zipfile
import threading
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from desk_companion import local_sources as sources, knowledge, wiki_connection, assistant
from desk_companion.board_workbench import BoardWorkbench


class LocalSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {'DESK_COMPANION_DATA_DIR': self.temp.name}); env.start(); self.addCleanup(env.stop)

    def file(self, name='资料.md', body='## 标题\n完整正文\n最后一行'):
        path = self.root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body.encode('utf-8'))
        return path

    def test_nested_folder_inventory_skips_dependencies_and_reports_unreadable_files(self):
        folder = self.root/'资料'; folder.mkdir()
        self.file('资料/a.md'); self.file('资料/子目录/b.py', 'print("hello")')
        self.file('资料/node_modules/ignore.md'); self.file('资料/.private/hide.md')
        (folder/'binary.txt').write_bytes(b'\0binary')
        (folder/'photo.jpg').write_bytes(b'unsupported')
        result = sources.stage([str(folder)], 'folder')
        group = result['sources'][0]
        self.assertEqual([f['relative'] for f in group['files']], ['a.md', str(Path('子目录')/'b.py')])
        self.assertEqual(group['skipped'], 1)
        self.assertEqual(result['errors'][0]['name'], 'binary.txt')
        block = sources.turn_block([group['id']])
        self.assertIn(group['files'][0]['id'], block)
        self.assertNotIn(sources.get(group['files'][0]['id'])['text'], block)

    def test_large_file_is_read_without_truncation_and_attachment_context_is_bounded(self):
        body = '大文件内容\n' * 5000 + '完整结尾'
        source = sources.stage([str(self.file(body=body))])['sources'][0]
        block = sources.turn_block([source['id']])
        self.assertNotIn(body, block); self.assertIn('read_local_source', block)
        got, offset = '', 0
        while True:
            page = sources.read_page(source['id'], offset, 12000); got += page['text']
            if page['next_offset'] is None: break
            offset = page['next_offset']
        self.assertTrue(got == body, '分页结果与实际文件正文不一致')
        self.assertFalse(page['complete'])
        self.assertEqual(sources.get(source['id'])['text'], body)

    def test_selection_cancel_has_no_side_effect_and_snapshot_ids_cannot_be_paths(self):
        process = SimpleNamespace(returncode=0, communicate=lambda: ('[]', ''), poll=lambda: 0)
        with patch.object(sources.subprocess, 'Popen', return_value=process):
            self.assertEqual(sources.pick('file')['sources'], [])
        self.assertFalse(sources.root().exists())
        for ident in ('../outside', str(self.root), '', 'x'*32):
            with self.assertRaises(ValueError): sources.get(ident)

    def test_size_and_folder_limits_are_explicit(self):
        path = self.file()
        with patch.object(sources, 'MAX_BYTES', 1):
            result = sources.stage([str(path)])
            self.assertFalse(result['sources']); self.assertIn('20 MB', result['errors'][0]['error'])
        self.file('two.md')
        with patch.object(sources, 'MAX_FILES', 1):
            with self.assertRaisesRegex(ValueError, '300'): sources.stage([str(self.root)], 'folder')

    def test_cancel_before_request_arrives_does_not_open_picker(self):
        ident = uuid.uuid4().hex
        sources.cancel(ident)
        with patch.object(sources.subprocess, 'Popen') as create:
            self.assertTrue(sources.pick('folder', ident)['cancelled'])
        create.assert_not_called()

    def test_cancel_interrupts_folder_import_before_snapshots_are_written(self):
        folder = self.root/'资料'; folder.mkdir(); self.file('资料/a.md'); self.file('资料/b.md')
        ident = uuid.uuid4().hex
        extract = sources.extract
        def cancel_during_file(path):
            result = extract(path); sources.cancel(ident); return result
        with patch.object(sources, 'extract', side_effect=cancel_during_file) as read:
            result = sources.stage([str(folder)], 'folder', ident)
        self.assertTrue(result['cancelled']); self.assertEqual(read.call_count, 1)
        self.assertFalse(sources.root().exists())

    def test_cancel_stops_only_owned_picker_process_and_reaps_it(self):
        original = subprocess.Popen
        ready = threading.Event(); processes = []; result = []
        ident = uuid.uuid4().hex
        def helper(*args, **kwargs):
            process = original([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
            processes.append(process); ready.set(); return process
        with patch.object(sources.subprocess, 'Popen', side_effect=helper):
            worker = threading.Thread(target=lambda: result.append(sources.pick('file', ident)))
            worker.start(); self.assertTrue(ready.wait(5)); sources.cancel(ident); worker.join(5)
            if worker.is_alive():
                processes[0].kill(); worker.join(5)
            self.assertFalse(worker.is_alive()); self.assertTrue(result[0]['cancelled'])
            self.assertIsNotNone(processes[0].poll())

    @unittest.skipUnless(os.name == 'nt', 'Windows common dialog integration')
    def test_modern_dialog_initializes_with_per_monitor_dpi_without_showing_ui(self):
        for kind in ('file', 'folder'):
            probe = subprocess.run([sys.executable, '-m', 'desk_companion.local_picker', '--probe', kind],
                capture_output=True, text=True, encoding='utf-8', timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(probe.returncode, 0, probe.stderr)
            info = json.loads(probe.stdout)
            self.assertTrue(info['modern_dialog']); self.assertTrue(info['per_monitor_v2'])
            self.assertEqual(bool(info['options'] & 0x20), kind == 'folder')

    def test_office_formats_extract_text_without_executing_files(self):
        doc = self.root/'word.docx'
        with zipfile.ZipFile(doc, 'w') as z:
            z.writestr('word/document.xml', '<w:document xmlns:w="urn:w"><w:p><w:r><w:t>第一段</w:t></w:r></w:p><w:p><w:r><w:t>最后段</w:t></w:r></w:p></w:document>')
        self.assertEqual(sources.extract(doc), '第一段\n最后段')
        ppt = self.root/'deck.pptx'
        with zipfile.ZipFile(ppt, 'w') as z:
            z.writestr('ppt/slides/slide10.xml', '<slide><t>第十页</t></slide>')
            z.writestr('ppt/slides/slide2.xml', '<slide><t>第二页</t></slide>')
        self.assertLess(sources.extract(ppt).index('第二页'), sources.extract(ppt).index('第十页'))
        sheet = self.root/'sheet.xlsx'
        with zipfile.ZipFile(sheet, 'w') as z:
            z.writestr('xl/sharedStrings.xml', '<sst><si><t>标题</t></si></sst>')
            z.writestr('xl/worksheets/sheet1.xml', '<worksheet><row><c r="A1" t="s"><v>0</v></c><c r="B1"><f>1+2</f><v>3</v></c></row></worksheet>')
        self.assertIn('标题', sources.extract(sheet)); self.assertIn('1+2', sources.extract(sheet))

    def test_local_index_rebuild_reads_updated_file_and_keeps_source_group(self):
        path = self.file(); source = sources.stage([str(path)])['sources'][0]
        with patch.object(knowledge, '_require_models'), patch.object(knowledge, '_embed', side_effect=lambda texts: [[1.0, 0.0] for _ in texts]):
            page = knowledge.add_doc('local:'+source['id'], source['name'])
            self.assertEqual(page['docs'][0]['source'], 'local')
            path.write_text('更新后的完整文字', 'utf-8')
            updated = knowledge.rebuild()
            self.assertIn('更新后的完整文字', updated['chunks'][0]['text'])
            another = sources.stage([str(path)])['sources'][0]
            with self.assertRaisesRegex(RuntimeError, '已经在库'): knowledge.add_doc('local:'+another['id'], source['name'])

    def test_pdf_text_and_scanned_pdf_are_distinguished(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter(); page = writer.add_blank_page(width=200, height=200)
        font = DictionaryObject({NameObject('/Type'):NameObject('/Font'), NameObject('/Subtype'):NameObject('/Type1'), NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream = DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 10 10 Td (Actual local PDF text) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        path = self.root/'text.pdf'; writer.write(path)
        self.assertIn('Actual local PDF text', sources.extract(path))
        blank = PdfWriter(); blank.add_blank_page(width=200, height=200); blank.write(self.root/'scan.pdf')
        with self.assertRaisesRegex(ValueError, 'OCR'): sources.extract(self.root/'scan.pdf')

    def test_attachment_reaches_llm_and_raw_body_is_not_written_into_history(self):
        from atlas.testing import FakeLLM
        from desk_companion.local_api.host import HeadlessApp
        host = HeadlessApp()
        with patch.object(assistant, 'require_llm_env', return_value={'ATLAS_API_KEY':'test','ATLAS_BASE_URL':'http://example.invalid','ATLAS_MODEL':'fake'}):
            host.agent = assistant.build_agent(host)
        seen = []
        fake = FakeLLM(['已根据附件回答。'])
        original = fake.chat
        def chat(messages, *args, **kwargs):
            seen.extend(messages); return original(messages, *args, **kwargs)
        fake.chat = chat; host.agent.llm = fake
        source = sources.stage([str(self.file(body='附件实际文字，完整结尾'))])['sources'][0]
        with patch.object(host.conversation, 'engage_model'):
            answer = host.run_chat('解释这份资料', {'attachments':[source['id']]}, lambda _: None, lambda _: None,
                                   {'reasoning_effort':'low','temperature':0.5,'top_p':1}, lambda _: None)
        self.assertIn('已根据附件', answer)
        self.assertIn('附件实际文字，完整结尾', json.dumps(seen, ensure_ascii=False))
        from desk_companion.memory import list_chat
        self.assertNotIn('附件实际文字', json.dumps(list_chat(host.state.session_id), ensure_ascii=False))


class WikiGroupTests(unittest.TestCase):
    setUp = LocalSourceTests.setUp
    def test_partial_catalog_preserves_loaded_nodes_and_marks_network_failure(self):
        node = {'node_token':'n1','obj_token':'d1','title':'已读取','obj_type':'docx','has_child':True}
        with patch.object(wiki_connection, 'cli', side_effect=[{'space_id':'s1'}, {'nodes':[node]}, RuntimeError('TLS timeout private-secret')]):
            with self.assertRaises(wiki_connection.CatalogIncomplete) as failure:
                wiki_connection.catalog({'wiki_url':'https://example.feishu.cn/wiki/root'})
        self.assertEqual(failure.exception.items[0]['title'], '已读取')
        self.assertIn('未加载完整', str(failure.exception))
        self.assertNotIn('private-secret', str(failure.exception))

    def test_real_space_ids_names_and_legacy_index_metadata_are_preserved(self):
        items = [{'title':'技术文档','url':'https://example.feishu.cn/wiki/n1','token':'d1'},
                 {'title':'日记','url':'https://example.feishu.cn/docx/d2','token':'d2'},
                 {'title':'视频笔记','url':'https://example.feishu.cn/wiki/n3','token':'d3','space_id':'s2'}]
        with patch.object(wiki_connection, 'cli', side_effect=[{'spaces':[{'space_id':'s1','name':'技术库'},{'space_id':'s2','name':'视频库'}]}, {'space_id':'s1'}]):
            result = wiki_connection.enrich_catalog(items)
        self.assertEqual([r['space_name'] for r in result], ['技术库','飞书云文档','视频库'])
        self.assertEqual(wiki_connection.cached_source('d1')['space_id'], 's1')
        self.assertEqual(knowledge._source_metadata({'id':items[0]['url']})['space_name'], '技术库')
        with patch.object(wiki_connection, 'cli') as cli:
            wiki_connection.enrich_catalog(items)
            cli.assert_not_called()
