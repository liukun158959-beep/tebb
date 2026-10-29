from pathlib import Path
import tempfile
import unittest
import zipfile
from scripts.release_audit import audit, audit_names


class AuditTests(unittest.TestCase):
    def test_rejects_user_data_and_private_assets_anywhere_in_package(self):
        for name in ('runtime/Lib/site-packages/memory/workspace.sqlite3', 'cache/usage.jsonl',
                     'memory/today.json', '.env.backup', 'user_state.before-upgrade.json',
                     'feishu-connection.json', 'MODELS.JSON', 'assets/skins/x/model.model3.json', '../escape.py',
                     'C:/escape.py', 'public.txt:stream', 'nested/.git/config', '.npmrc'):
            with self.subTest(name=name),self.assertRaises(ValueError):audit_names([name])
        self.assertEqual(audit_names(['runtime/Lib/site-packages/desk_companion/ui/release.json',
                                     'runtime/Lib/site-packages/openai/models.py','runtime/python.exe',
                                     'runtime/Lib/site-packages/atlas/core/loop.py',
                                     'runtime/Lib/site-packages/numpy/core/__init__.py']),5)

    def test_zip_and_directory_share_policy_without_reading_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'public.txt').write_text('public')
            self.assertEqual(audit(root)['files'],1)
            (root/'models.json').write_text('do-not-log-fixture-secret')
            with self.assertRaises(ValueError) as exc:audit(root)
            self.assertNotIn('do-not-log-fixture-secret',str(exc.exception))
            package=root/'package.zip'
            with zipfile.ZipFile(package,'w') as archive:archive.writestr('memory/chat.jsonl','private')
            with self.assertRaises(ValueError):audit(package)
