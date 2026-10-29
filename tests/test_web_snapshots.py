import json
import os
import socket
import tempfile
import unittest
from unittest.mock import patch

from desk_companion import web_sources as sources
from desk_companion.web_snapshots import PageReader
from tests.test_web_sources import Response


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ,DESK_COMPANION_DATA_DIR=self.temp.name).start()
        patch.object(sources.socket,'getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]).start()
        self.body=('开头资料\n'*2400)+'\n尾部技术指标\n准确尾部结论需要补读。'*120
        self.open=patch.object(sources,'_open',side_effect=lambda *a:Response('<main>'+self.body+'</main>')).start()
        self.reader=PageReader('a'*32);self.url='https://example.org/article'

    def test_remainder_reads_same_immutable_snapshot_and_coverage_upgrades_only_after_read(self):
        first=self.reader.read(self.url)
        self.assertTrue(first['truncated']);self.assertEqual(len(first['text']),12000)
        # A resumed worker reuses the snapshot despite changed remote contents.
        self.open.side_effect=AssertionError('must not fetch a different revision')
        resumed=PageReader('a'*32,[first]);second=resumed.read(self.url,offset=first['next_offset'])
        self.assertIn('尾部技术指标',second['text']);self.assertEqual(first['sha256'],second['sha256'])
        ledger=sources.source_catalog([first,second]);self.assertFalse(ledger[0]['truncated'])
        self.assertEqual(ledger[0]['read_ranges'],[[0,first['characters']]])
        self.assertFalse(sources.check_citations('[来源](https://example.org/article)',[first,second])['matched'][0]['truncated'])
        self.assertEqual(self.open.call_count,1)

    def test_keyword_and_section_read_auditable_ranges_without_claiming_whole_page(self):
        first=self.reader.read(self.url)
        piece=self.reader.read(self.url,snapshot_id=first['snapshot_id'],section='尾部技术指标')
        self.assertGreater(piece['read_start'],0);self.assertTrue(piece['truncated'])
        self.assertIn('准确尾部结论',piece['text'])
        with self.assertRaisesRegex(ValueError,'未找到'):self.reader.read(self.url,query='不存在的术语')
        for bad in [-1,True,999999]:
            with self.subTest(offset=bad),self.assertRaises(ValueError):self.reader.read(self.url,offset=bad)

    def test_cross_task_snapshot_paths_and_corrupt_snapshot_are_rejected(self):
        first=self.reader.read(self.url);ident=first['snapshot_id']
        with self.assertRaises(ValueError):PageReader('b'*32).read(self.url,snapshot_id=ident)
        with self.assertRaises(ValueError):self.reader.read('https://other.org/',snapshot_id=ident)
        with self.assertRaises(ValueError):self.reader.read(self.url,snapshot_id='../secret')
        path=self.reader.path(ident);value=json.loads(path.read_text('utf-8'));value['text']='伪造内容';path.write_text(json.dumps(value),'utf-8')
        with self.assertRaisesRegex(ValueError,'完整性'):self.reader.read(self.url,snapshot_id=ident)
        self.assertEqual(self.open.call_count,1)
