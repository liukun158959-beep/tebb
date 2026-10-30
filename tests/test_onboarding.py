import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desk_companion import onboarding
from desk_companion.model_validation import validate_base_url


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"DESK_COMPANION_DATA_DIR": str(self.root),
                                "DESK_COMPANION_ASSET_DIR": str(self.root / "assets")}).start()
        self.catalog = {"active": "", "items": []}
        patch.object(onboarding, "public_catalog", side_effect=lambda: self.catalog).start()

    def test_empty_install_cannot_complete_without_a_saved_model(self):
        self.assertTrue(onboarding.status()["show"])
        self.assertFalse(onboarding.complete()['ok'])
        self.assertFalse((self.root / 'onboarding.json').exists())
        self.assertTrue(onboarding.status()["show"])

    def test_pause_resumes_optional_stage_without_storing_form_fields(self):
        self.catalog = {'active': 'm', 'items': [{'id': 'm', 'has_key': True}]}
        self.assertTrue(onboarding.save_progress('feishu')['ok'])
        self.assertEqual(onboarding.status()['step'], 'feishu')
        self.assertTrue(onboarding.status()['show'])
        self.assertEqual(set(json.loads((self.root / 'onboarding.json').read_text())), {'version', 'completed', 'step'})
        self.assertTrue(onboarding.complete()['ok'])
        self.assertFalse(onboarding.status()['show'])

    def test_invalid_progress_does_not_overwrite_a_valid_checkpoint(self):
        onboarding.save_progress('terminal')
        for value in ('unknown', '../settings', None, {}, ['model']):
            self.assertFalse(onboarding.save_progress(value)['ok'])
        self.assertEqual(onboarding.status()['step'], 'terminal')

    def test_legacy_completed_and_damaged_checkpoint_keep_settings_available(self):
        self.catalog = {'active': 'm', 'items': [{'id': 'm', 'has_key': True}]}
        path = self.root / 'onboarding.json'
        for raw in ('{"version":1,"completed":true}', '{broken', '[]'):
            path.write_text(raw, encoding='utf-8')
            self.assertFalse(onboarding.status()['show'])
            self.assertEqual(onboarding.status()['step'], 'model')

    def test_existing_config_is_preserved_and_not_forced_through_guide(self):
        self.catalog = {"active": "m", "items": [{"id": "m", "has_key": True}]}
        result = onboarding.status()
        self.assertFalse(result["show"])
        self.assertTrue(result["configured"])
        self.assertNotIn("api_key", json.dumps(result))
        self.assertEqual(Path(result["data_dir"]), self.root.resolve())
        self.assertTrue(result["checks"]["pet"])
        self.assertFalse(result["checks"]["live2d"])

    def test_api_url_rejects_endpoint_and_secrets(self):
        for raw in ["api.example.com", "https://", "file:///a", "https://u:p@example.com/v1",
                    "https://example.com/v1?key=private", "https://example.com/v1/chat/completions",
                    "https://example.com/v1/responses", "https://example.com:wrong/v1"]:
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                validate_base_url(raw)
        self.assertEqual(validate_base_url(" https://example.com/v1/ "), "https://example.com/v1")
        self.assertEqual(validate_base_url("http://127.0.0.1:8080/v1"), "http://127.0.0.1:8080/v1")
