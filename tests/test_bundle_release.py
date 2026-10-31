import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


class BundleArchiveTests(unittest.TestCase):
    def test_captured_nsis_input_matches_only_official_marker_change(self):
        import hashlib
        from scripts.capture_installer_binary import capture,PORTABLE_MARKER,NSIS_MARKER
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);source=root/'source.exe';destination=root/'captured.exe'
            portable=b'MZ synthetic compiled input '+PORTABLE_MARKER+b' payload'
            patched=portable.replace(PORTABLE_MARKER,NSIS_MARKER);source.write_bytes(patched)
            expected=hashlib.sha256(portable).hexdigest()
            self.assertEqual(capture(source,destination,expected),hashlib.sha256(patched).hexdigest())
            self.assertEqual(destination.read_bytes(),patched);self.assertEqual(source.read_bytes(),patched)
            with self.assertRaises(FileExistsError):capture(source,destination,expected)
            source.write_bytes(patched+b' unexpected change')
            with self.assertRaises(ValueError):capture(source,root/'rejected.exe',expected)
            self.assertFalse((root/'rejected.exe').exists())

    def test_common_payload_preserves_actual_installed_inventory(self):
        from scripts import bundle_release as bundle
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);package=root/'package';package.mkdir()
            config=root/'client/src-tauri/tauri.conf.json';config.parent.mkdir(parents=True);config.write_text('{"productName":"tebb"}')
            inventory=package/'runtime-packages.txt';inventory.write_text('actual-installed==1\n')
            for name in ['docs/GETTING_STARTED.md','docs/RELEASING.md','scripts/安装知识库扩展.cmd','scripts/runtime-lock.txt']:
                file=root/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('source constraints')
            exe=root/'program.exe';exe.write_bytes(b'synthetic exe')
            with patch.object(bundle,'ROOT',root),patch.object(bundle,'release_binary',return_value=exe),patch.object(bundle,'binary_identity',return_value=dict(name='tebb',version=bundle.VERSION)),patch.object(bundle,'generate_licenses',return_value=0),patch.object(bundle,'audit'),patch.object(bundle.subprocess,'check_output',return_value='a'*40):
                bundle.prepare_package(package)
            self.assertEqual(inventory.read_text(),'actual-installed==1\n')
            self.assertEqual((package/'runtime-constraints.txt').read_text(),'source constraints')

    def test_combined_checksums_preserve_both_assets_and_reject_stale_bytes(self):
        from scripts.bundle_release import write_checksums,checked_sums
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);archive=root/'fixture.zip';archive.write_bytes(b'zip')
            installer=root/'fixture-setup.exe';installer.write_bytes(b'setup')
            write_checksums(root,[archive]);write_checksums(root,[installer])
            self.assertEqual(set(checked_sums(root)),{archive.name,installer.name})
            before=(root/'SHA256SUMS.txt').read_bytes();archive.write_bytes(b'tampered')
            with self.assertRaises(ValueError):write_checksums(root,[installer])
            self.assertEqual((root/'SHA256SUMS.txt').read_bytes(),before)

    def test_installer_resource_map_matches_portable_cache_exclusions(self):
        from scripts.bundle_installer import resources
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);(root/'runtime/__pycache__').mkdir(parents=True)
            (root/'runtime/__pycache__/cache.pyc').write_bytes(b'cache')
            (root/'tebb.exe').write_bytes(b'program');(root/'runtime/python.exe').write_bytes(b'runtime')
            (root/'使用说明.md').write_text('synthetic')
            mapped=resources(root)
            self.assertCountEqual(mapped.values(),['runtime/python.exe','使用说明.md'])

    def test_upstream_notice_with_1970_timestamp_retains_bytes_in_portable_zip(self):
        scripts=Path(__file__).resolve().parents[1]/'scripts'
        with patch.object(sys,'path',[str(scripts),*sys.path]):
            write_archive=runpy.run_path(str(scripts/'bundle_release.py'))['write_archive']
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);package=root/'package';package.mkdir()
            notice=package/'LICENSE';payload=b'Original upstream copyright\n'
            notice.write_bytes(payload);os.utime(notice,(1,1))
            (package/'__pycache__').mkdir();(package/'__pycache__/cache.pyc').write_bytes(b'cache')
            output=root/'candidate.zip';write_archive(package,output,'tebb-fixture')
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.namelist(),['tebb-fixture/LICENSE'])
                self.assertEqual(archive.read('tebb-fixture/LICENSE'),payload)
                self.assertEqual(archive.getinfo('tebb-fixture/LICENSE').date_time,(1980,1,1,0,0,0))
