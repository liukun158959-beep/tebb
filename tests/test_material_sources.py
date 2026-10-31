import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from desk_companion import local_sources, material_sources as sources, project_workspace


class MaterialSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, DESK_COMPANION_DATA_DIR=str(self.root/'profile'))
        self.env.start(); self.addCleanup(self.env.stop)
        self.file = self.root/'one.txt'; self.file.write_text('first version', encoding='utf-8')

    def select(self, path=None):
        return local_sources.stage([str(path or self.file)])['sources'][0]['id']

    def register(self, project_id=''):
        return sources.register(title='One', snapshot_id=self.select(), project_id=project_id)

    def test_duplicate_selection_reuses_identity_and_versions_remain_immutable(self):
        one = self.register(); two = self.register()
        self.assertEqual(one['id'], two['id'])
        observed, _ = sources.observe(one['id'])
        old = observed['observed_version']
        self.file.write_text('second version', encoding='utf-8')
        fresh, _ = sources.observe(one['id'])
        self.assertNotEqual(old, fresh['observed_version'])
        self.assertEqual(sources.read_version(one['id'], old)['text'], 'first version')

    def test_rebind_preserves_identity_without_moving_or_deleting_files(self):
        one = self.register(); sources.observe(one['id'])
        moved = self.file.with_name('renamed.txt'); self.file.rename(moved)
        with self.assertRaises(FileNotFoundError): sources.observe(one['id'])
        self.assertEqual(sources.get(one['id'])['state'], 'missing')
        rebound = sources.rebind(one['id'], '', self.select(moved), one['revision'])
        self.assertEqual(rebound['id'], one['id']); self.assertEqual(rebound['doc_id'], one['doc_id'])
        self.assertTrue(moved.is_file()); self.assertFalse(self.file.exists())
        self.assertEqual(sources.observe(one['id'])[1], 'first version')

    def test_projects_are_filtered_and_old_grant_is_rejected(self):
        project_workspace.save(str(self.root), project_id='one')
        project_workspace.save(str(self.root), project_id='two')
        one = self.register('one')
        self.assertEqual(sources.listing('two'), [])
        with self.assertRaises(ValueError): sources.get(one['id'], 'two')
        sources.observe(one['id'], 'one')
        project_workspace.save(str(self.root), project_id='one')
        with self.assertRaises(PermissionError): sources.observe(one['id'], 'one')
        self.assertEqual(sources.get(one['id'], 'one')['state'], 'revoked')

    def test_cancel_before_start_and_during_operation_prevent_commit(self):
        one = self.register(); operation = uuid.uuid4().hex
        sources.cancel(one['id'], '', operation)
        with self.assertRaises(InterruptedError): sources.begin(one['id'], '', operation, 'index')
        operation = uuid.uuid4().hex; sources.begin(one['id'], '', operation, 'index')
        row, _ = sources.observe(one['id'], operation_id=operation)
        sources.cancel(one['id'], '', operation)
        with self.assertRaises(InterruptedError):
            with sources.commit_guard(one['id'], '', row['revision'], row['observed_version'], operation):
                self.fail('cancelled update cannot commit')

    def test_source_change_after_embedding_prevents_index_commit(self):
        one = self.register(); operation = uuid.uuid4().hex
        sources.begin(one['id'], '', operation, 'index')
        old, _ = sources.observe(one['id'], operation_id=operation)
        self.file.write_text('changed during embedding', encoding='utf-8')
        sources.observe(one['id'])
        with self.assertRaises(ValueError):
            with sources.commit_guard(one['id'], '', old['revision'], old['observed_version'], operation):
                self.fail('changed update cannot commit')

    def test_cross_project_path_and_corrupt_snapshot_are_rejected(self):
        folder = self.root/'project'; folder.mkdir()
        project_workspace.save(str(folder), project_id='one')
        with self.assertRaises(ValueError): self.register('one')
        one = self.register(); row, _ = sources.observe(one['id'])
        path = sources.root()/'versions'/one['id']/(row['observed_version']+'.json')
        data = json.loads(path.read_text('utf-8')); data['text'] = 'tampered'
        path.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(ValueError): sources.read_version(one['id'], row['observed_version'])
        with self.assertRaises(ValueError): sources.observe(one['id'])

    def test_completed_operation_id_is_not_implicitly_replayed(self):
        one = self.register(); operation = uuid.uuid4().hex
        sources.begin(one['id'], '', operation, 'check'); sources.observe(one['id'], operation_id=operation)
        sources.finish(one['id'], '', operation)
        with self.assertRaisesRegex(ValueError, '不要直接重放'): sources.begin(one['id'], '', operation, 'check')

    def test_cancelled_operation_remains_cancelled_after_marker_window_changes(self):
        one = self.register(); operation = uuid.uuid4().hex
        sources.begin(one['id'], '', operation, 'refresh')
        observed, _ = sources.observe(one['id'], operation_id=operation)
        sources.cancel(one['id'], '', operation)
        for _ in range(65): sources.cancel(one['id'], '', uuid.uuid4().hex)
        with self.assertRaises(InterruptedError):
            with sources.commit_guard(one['id'], '', observed['revision'], observed['observed_version'], operation):
                self.fail('cancelled operation cannot become active after marker eviction')


if __name__ == '__main__': unittest.main()
