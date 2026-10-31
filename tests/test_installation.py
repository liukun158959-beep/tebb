import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from desk_companion import installation as installer,runtime_extensions as extensions
from desk_companion.resource_lock import ResourceLock


class InstallerDataTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.base=Path(self.folder.name).resolve()
        for name in installer.OWNED_FOLDERS:
            path=self.base/name;path.mkdir();(path/'synthetic.txt').write_text(name)

    def test_clear_only_known_roots_and_ignore_custom_data_override(self):
        outside=self.base/'external-project';outside.mkdir();(outside/'keep.txt').write_text('retain')
        with patch.dict(os.environ,{'DESK_COMPANION_DATA_DIR':str(outside)}):value=installer.clear_data(self.base)
        self.assertTrue(value['ok']);self.assertEqual([r['state']for r in value['roots']],['removed','removed'])
        self.assertEqual((outside/'keep.txt').read_text(),'retain')
        self.assertTrue(installer.clear_data(self.base)['ok'])

    def test_runtime_lease_blocks_cleanup_without_removing_data(self):
        with ResourceLock('runtime-process',directory=self.base/'DeskCompanion/memory/locks').hold():
            with self.assertRaises(BlockingIOError):installer.clear_data(self.base)
        self.assertTrue((self.base/'DeskCompanion/synthetic.txt').exists())

    def test_busy_second_root_rolls_back_before_any_delete(self):
        original=Path.rename
        def rename(path,target):
            if path.name=='cn.deskcompanion.client':raise PermissionError('synthetic busy browser')
            return original(path,target)
        with patch.object(Path,'rename',rename):value=installer.clear_data(self.base)
        self.assertFalse(value['ok']);self.assertTrue(all(r['state']=='preserved'for r in value['roots']))
        for name in installer.OWNED_FOLDERS:self.assertTrue((self.base/name/'synthetic.txt').exists())

    def test_outside_path_is_rejected(self):
        with self.assertRaises(ValueError):installer.validate_tree(self.base.parent/'unowned',self.base)

    def test_report_collision_prevents_any_cleanup(self):
        report=self.base/'previous-report.json';report.write_text('preserve original report')
        with patch.object(installer,'local_appdata',return_value=self.base),patch.object(sys,'argv',['installation','clear-default-data','--confirm-default-data','--report',str(report)]):
            with self.assertRaises(FileExistsError):installer.main()
        self.assertEqual(report.read_text(),'preserve original report')
        for name in installer.OWNED_FOLDERS:self.assertTrue((self.base/name/'synthetic.txt').exists())

    @unittest.skipUnless(os.name=='nt','Windows junction semantics')
    def test_nested_junction_is_rejected_without_following_or_deleting_target(self):
        outside=self.base/'external';outside.mkdir();(outside/'keep.txt').write_text('retain')
        junction=self.base/'DeskCompanion/junction'
        script="New-Item -ItemType Junction -Path '"+str(junction).replace("'","''")+"' -Target '"+str(outside).replace("'","''")+"' | Out-Null"
        subprocess.run(['powershell','-NoProfile','-Command',script],check=True,capture_output=True,timeout=15)
        with self.assertRaises(ValueError):installer.clear_data(self.base)
        self.assertEqual((outside/'keep.txt').read_text(),'retain')
        self.assertTrue((self.base/'cn.deskcompanion.client/synthetic.txt').exists())


class ExtensionLayerTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.base=Path(folder.name)
        self.package=self.base/'program';self.package.mkdir();(self.package/'runtime-packages.txt').write_text('fixture==1\n')
        self.root=self.base/'user';self.root.mkdir();self.calls=[]

    def runner(self,args,**kwargs):
        self.calls.append((args,kwargs));self.assertEqual(kwargs['env']['TEBB_DISABLE_USER_EXTENSIONS'],'1')
        if 'download'in args:
            folder=Path(args[args.index('--dest')+1]);(folder/'torch-1.0+cpu-py3-none-any.whl').write_bytes(b'synthetic wheel')
        if '--target'in args:
            target=Path(args[args.index('--target')+1]);dist=target/'torch-1.0+cpu.dist-info';dist.mkdir(exist_ok=True)
            self.assertEqual(kwargs['env']['TEMP'],extensions.filesystem_path(target.parent/'t'))
            self.assertEqual(kwargs['env']['TMP'],kwargs['env']['TEMP']);self.assertIn('--no-compile',args)
            (dist/'METADATA').write_text('Name: torch\nVersion: 1.0+cpu\n')
            (target/'fixture_optional.py').write_text('VALUE=174\n')

    def test_publish_verified_generation_and_preserve_previous_on_failure(self):
        first=extensions.install(package=self.package,root=self.root,run=self.runner)
        path=extensions.active_path(self.package,self.root);self.assertEqual(path.parent.name,first['generation'])
        self.assertIn('torch==1.0+cpu',(path.parent/'torch-constraint.txt').read_text())
        download=next(args for args,_ in self.calls if 'download'in args)
        self.assertIn('https://download.pytorch.org/whl/cpu',download);self.assertIn('--no-deps',download)
        for args,_ in self.calls:
            if '--target'in args:
                self.assertNotIn('--index-url',args);self.assertIn(str(self.package/'runtime-packages.txt'),args)
        def fail(args,**kwargs):
            self.runner(args,**kwargs)
            if args[-1]=='sentence-transformers':raise subprocess.CalledProcessError(1,args)
        with self.assertRaises(subprocess.CalledProcessError):extensions.install(package=self.package,root=self.root,run=fail)
        self.assertEqual(extensions.active_path(self.package,self.root),path)
        states=[json.loads(p.read_text())['state']for p in path.parent.parent.glob('*/result.json')]
        self.assertCountEqual(states,['ready','failed'])

    def test_non_cpu_wheel_is_rejected_before_install_or_publication(self):
        def download(args,**kwargs):
            self.calls.append((args,kwargs))
            folder=Path(args[args.index('--dest')+1]);(folder/'torch-1.0-py3-none-any.whl').write_bytes(b'synthetic non CPU wheel')
        with self.assertRaisesRegex(RuntimeError,'CPU版'):extensions.install(package=self.package,root=self.root,run=download)
        self.assertIsNone(extensions.active_path(self.package,self.root))
        self.assertTrue(all('download'in args for args,_ in self.calls))

    def test_base_dependency_change_keeps_old_layer_without_loading_it(self):
        extensions.install(package=self.package,root=self.root,run=self.runner)
        old=extensions.active_path(self.package,self.root)
        (self.package/'runtime-packages.txt').write_text('fixture==2\n')
        self.assertIsNone(extensions.active_path(self.package,self.root));self.assertTrue(old.is_dir())

    def test_unvalidated_and_traversal_pointers_do_not_activate(self):
        layer=extensions.layer_root(self.package,self.root);layer.mkdir(parents=True)
        pointer=layer/'current.json';pointer.write_text(json.dumps(dict(version=1,generation='../external')))
        with self.assertRaises(ValueError):extensions.active_path(self.package,self.root)
        ident='a'*16;target=layer/ident/'p';target.mkdir(parents=True)
        pointer.write_text(json.dumps(dict(version=1,generation=ident)))
        (target.parent/'result.json').write_text(json.dumps(dict(state='failed',compatibility=layer.name)))
        with self.assertRaises(ValueError):extensions.active_path(self.package,self.root)


class VerificationBoundaryTests(unittest.TestCase):
    def test_lifecycle_driver_refuses_a_production_installer_before_running_it(self):
        from scripts.verify_installer import read_candidate
        import hashlib
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);exe=root/'fixture.exe';exe.write_bytes(b'not executable')
            (root/'installer-build.json').write_text(json.dumps(dict(version=2,verification=False,identifier='cn.deskcompanion.client',product_name='tebb',installer_sha256=hashlib.sha256(exe.read_bytes()).hexdigest())))
            with self.assertRaisesRegex(ValueError,'仅接受'):read_candidate(exe)

    def test_lifecycle_driver_refuses_state_pointing_outside_owned_work(self):
        from scripts.verify_installer import load_state
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder).resolve()
            (root/'state.json').write_text(json.dumps(dict(version=1,verification=True,install_dir=str(root.parent/'unowned'))))
            with self.assertRaisesRegex(ValueError,'越界'):load_state(root)
