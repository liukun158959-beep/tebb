import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from desk_companion import knowledge as k, local_sources, material_service as service, material_sources as sources, project_workspace


class MaterialServiceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, DESK_COMPANION_DATA_DIR=str(self.root/'profile'))
        env.start(); self.addCleanup(env.stop)
        self.file = self.root/'source.txt'; self.file.write_text('original evidence', encoding='utf-8')
        for name, kwargs in [('_require_models', {}), ('_embed', dict(side_effect=lambda texts: [[1., 0.] for _ in texts])),
                             ('_rerank', dict(side_effect=lambda question, hits: hits))]:
            mock = patch.object(k, name, **kwargs); mock.start(); self.addCleanup(mock.stop)

    def register(self, project_id=''):
        selected = local_sources.stage([str(self.file)])['sources'][0]['id']
        result = service.operation(dict(action='register', snapshot_id=selected, title='Evidence', project_id=project_id))
        return next(row for row in result['sources'] if row['id'] == result['source_id'])

    def test_refresh_failure_keeps_index_and_remove_keeps_source_and_file(self):
        row = self.register(); args = dict(source_id=row['id'], action='refresh')
        service.operation(args); before = k._index_path().read_bytes()
        self.file.write_text('new evidence', encoding='utf-8')
        with patch.object(k, '_attach_vectors', side_effect=RuntimeError('embedding unavailable')):
            with self.assertRaises(k.KnowledgeChangeError) as error: service.operation(args)
        self.assertEqual(error.exception.details['index_change_state'], 'not_changed')
        self.assertEqual(k._index_path().read_bytes(), before)
        view = service.listing()['sources'][0]
        self.assertEqual(view['freshness'], 'stale'); self.assertEqual(view['operation']['state'], 'failed')
        service.operation(dict(action='remove_index', source_id=row['id']))
        self.assertTrue(self.file.is_file()); self.assertEqual(len(sources.listing()), 1)
        self.assertFalse(service.listing()['sources'][0]['indexed'])

    def test_project_query_filters_global_and_other_project_and_binds_citations(self):
        one = self.root/'one'; one.mkdir(); two = self.root/'two'; two.mkdir()
        self.file = one/'source.txt'; self.file.write_text('project one', encoding='utf-8')
        project_workspace.save(str(one), project_id='one'); project_workspace.save(str(two), project_id='two')
        row = self.register('one')
        service.operation(dict(action='refresh', source_id=row['id'], project_id='one'))
        found = k.retrieve_sources('evidence', project_id='one')
        self.assertTrue(found['kept']); self.assertEqual(found['kept'][0]['project_id'], 'one')
        _, cites = k._blocks(found['kept'], True)
        self.assertEqual(cites[0]['source_id'], row['id']); self.assertTrue(cites[0]['source_version'])
        self.assertEqual(k.public_view()['docs'], [])
        with self.assertRaises(RuntimeError): k.retrieve_sources('evidence', [row['doc_id']], project_id='two')
        with self.assertRaises(ValueError): service.operation(dict(action='remove_index', source_id=row['id'], project_id='two'))
        project_workspace.save(str(one), project_id='one')
        with self.assertRaises(RuntimeError): k.retrieve_sources('evidence', [row['doc_id']], project_id='one')

    def test_legacy_adoption_preserves_document_id_and_refreshes_only_one_source(self):
        selected = local_sources.stage([str(self.file)])['sources'][0]
        original = 'local:' + selected['id']; k.add_doc(original, 'Legacy')
        view = service.listing()['sources'][0]
        self.assertTrue(view['legacy']); self.assertEqual(view['freshness'], 'unknown')
        self.file.write_text('refreshed legacy', encoding='utf-8')
        service.operation(dict(action='refresh', source_id=view['id']))
        docs = k.indexed_documents(); self.assertEqual(len(docs), 1); self.assertEqual(docs[0]['id'], original)
        self.assertEqual(docs[0]['source_id'], view['id'])
        self.assertIn('refreshed legacy', k.public_view()['chunks'][0]['text'])

    def test_remote_refresh_keeps_existing_space_and_url_metadata(self):
        locator = 'https://example.feishu.cn/docx/fixture'
        with patch.object(k, '_fetch', return_value='remote original'):
            k.add_doc(locator, 'Remote', dict(source='wiki', space_id='space', space_name='Team docs'))
        source = service.listing()['sources'][0]
        with patch('desk_companion.feishu_docs.fetch_docx_markdown', return_value='remote updated'):
            service.operation(dict(action='refresh', source_id=source['id']))
        doc = k.indexed_documents()[0]
        self.assertEqual(doc['url'], locator); self.assertEqual(doc['space_id'], 'space')
        self.assertEqual(doc['space_name'], 'Team docs'); self.assertEqual(doc['source'], 'wiki')

    def test_index_committed_before_source_finalize_failure_is_not_reported_as_no_change(self):
        row = self.register()
        with patch.object(sources, 'finish', side_effect=sources.MaterialChangeError('fixture finalize failure')):
            with self.assertRaises(k.KnowledgeChangeError) as error:
                service.operation(dict(action='refresh', source_id=row['id']))
        self.assertEqual(error.exception.details['index_change_state'], 'committed')
        self.assertEqual(error.exception.details['source_change_state'], 'unconfirmed')
        self.assertEqual(k.indexed_documents()[0]['source_id'], row['id'])

    def test_external_file_change_during_embedding_does_not_commit_old_capture(self):
        row = self.register()
        def changed(chunks): self.file.write_text('changed after capture', encoding='utf-8')
        with patch.object(k, '_attach_vectors', side_effect=changed):
            with self.assertRaises(k.KnowledgeChangeError): service.operation(dict(action='refresh', source_id=row['id']))
        self.assertEqual(k.indexed_documents(), [])

    def test_old_attachment_entry_and_registered_source_converge_without_duplicate_index(self):
        row = self.register()
        selected = local_sources.stage([str(self.file)])['sources'][0]
        legacy_id = 'local:' + selected['id']; k.add_doc(legacy_id, 'Legacy entry')
        self.assertEqual(len(service.listing()['sources']), 1)
        service.operation(dict(action='refresh', source_id=row['id']))
        docs = k.indexed_documents(); self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]['id'], legacy_id); self.assertEqual(docs[0]['source_id'], row['id'])
        self.file.write_text('rebuild later', encoding='utf-8'); k.rebuild()
        self.assertEqual(len(k.indexed_documents()), 1)
        self.assertEqual(service.listing()['sources'][0]['freshness'], 'current')

    def test_changed_file_is_not_used_for_new_retrieval_but_old_version_remains_readable(self):
        row = self.register(); service.operation(dict(action='refresh', source_id=row['id']))
        version = k.indexed_documents()[0]['source_version']
        self.file.write_text('different body', encoding='utf-8')
        self.assertEqual(service.listing()['sources'][0]['freshness'], 'stale')
        with self.assertRaises(RuntimeError): k.retrieve_sources('evidence')
        self.assertEqual(sources.read_version(row['id'], version)['text'], 'original evidence')

    def test_saved_notebook_citation_retains_source_version(self):
        from desk_companion.notebook import _cites
        row = self.register(); service.operation(dict(action='refresh', source_id=row['id']))
        found = k.retrieve_sources('evidence')
        saved = _cites(found['cites'])
        self.assertEqual(saved[0]['source_id'], row['id']); self.assertTrue(saved[0]['source_version'])

    def test_invalid_action_does_not_adopt_legacy_and_version_read_is_read_only(self):
        selected = local_sources.stage([str(self.file)])['sources'][0]
        original = 'local:' + selected['id']; k.add_doc(original, 'Legacy')
        with self.assertRaises(ValueError): service.operation(dict(action='unsupported', source_id=sources.legacy_id(original)))
        self.assertFalse((sources.root()/'sources.json').exists())
        row = self.register(); observed, _ = sources.observe(row['id'])
        before = (sources.root()/'sources.json').read_bytes()
        result = service.operation(dict(action='read_version', source_id=row['id'], version=observed['observed_version']))
        self.assertEqual(result['source']['text'], 'original evidence')
        self.assertEqual((sources.root()/'sources.json').read_bytes(), before)


if __name__ == '__main__': unittest.main()
