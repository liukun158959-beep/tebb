from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import license_inventory as inventory
from scripts.release_audit import audit, audit_licenses


class LicenseInventoryTests(unittest.TestCase):
    def test_preserves_all_parts_of_installed_notices_without_absolute_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source';source.mkdir()
            (source/'LICENSE').write_text('ISC and Feather MIT original notices')
            (source/'NOTICE').write_text('Attribution')
            row=inventory.collect_package('frontend','lucide-react','1.17.0',source,root/'package/licenses','ISC')
            self.assertEqual(len(row['files']),2)
            self.assertTrue(all(not Path(p).is_absolute() for p in row['files']))
            self.assertEqual((root/'package'/row['files'][0]).read_text(),'ISC and Feather MIT original notices')

    def test_missing_text_requires_exact_version_and_preserves_upstream_declaration(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(inventory,'ROOT',Path(folder)):
            root=Path(folder);source=root/'source';source.mkdir()
            upstream=root/'licenses/supplemental/rust/example/1.0';upstream.mkdir(parents=True)
            (upstream/'LICENSE').write_text('Original upstream copyright')
            row=inventory.collect_package('rust','example','1.0',source,root/'package/licenses','MIT')
            self.assertEqual((root/'package'/row['files'][0]).read_text(),'Original upstream copyright')
            with self.assertRaisesRegex(ValueError,'Missing rust license'):
                inventory.collect_package('rust','example','2.0',source,root/'package/licenses','MIT')

    def test_supplemental_text_is_hash_checked(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(inventory,'ROOT',Path(folder)):
            root=Path(folder);(root/'licenses').mkdir()
            (root/'licenses/SOURCES.json').write_text('[{"path":"LICENSE","sha256":"invalid"}]')
            (root/'licenses/LICENSE').write_text('changed')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):inventory.verify_sources()

    def test_candidate_audit_cannot_pass_with_missing_legal_materials(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'LICENSE').write_text('MIT')
            self.assertEqual(audit(root)['files'],1)
            with self.assertRaisesRegex(ValueError,'缺少许可材料'):audit(root,require_licenses=True)

    def test_candidate_audit_rejects_inventory_pointing_at_missing_text(self):
        names={'LICENSE','THIRD_PARTY_NOTICES.md','licenses/SOURCES.json','licenses/dependencies.json',
               'runtime/LICENSE.txt','runtime/Lib/site-packages/atlas/licenses/Atlas-MIT.txt',
               'runtime/Lib/site-packages/atlas/licenses/HumanEval-MIT.txt',
               'runtime/Lib/site-packages/atlas/licenses/THIRD_PARTY_NOTICES.md'}
        body=b'[{"ecosystem":"frontend","files":["missing"]},{"ecosystem":"rust","files":[]},{"ecosystem":"python","files":[]}]'
        with self.assertRaisesRegex(ValueError,'缺失的许可文件'):audit_licenses(names,lambda name:body)
