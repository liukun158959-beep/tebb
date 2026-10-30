import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


class BundleArchiveTests(unittest.TestCase):
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
