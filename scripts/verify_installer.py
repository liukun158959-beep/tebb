"""Bounded lifecycle probes for an explicitly isolated verification installer."""
import argparse
import asyncio
from datetime import datetime,timedelta
import hashlib
import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import psutil
from desk_companion.resource_lock import ResourceLock
from desk_companion.runtime_instance import RUNTIME_LOCK_NAME
from desk_companion.installation import validate_tree
from desk_companion.calendar_time import TZ
from scripts.bundle_release import archive_hashes
from scripts.bundle_installer import installer_mutex_name


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_candidate(installer):
    installer=Path(installer).resolve();receipt=json.loads((installer.parent/'installer-build.json').read_text('utf-8'))
    if(receipt.get('version')!=2 or receipt.get('verification')is not True or not receipt.get('identifier','').startswith('cn.deskcompanion.verify174.')
        or not receipt.get('product_name','').startswith('tebb-verify174-')or digest(installer)!=receipt.get('installer_sha256')):
        raise ValueError('仅接受具有独立标识及有效摘要的验证安装器。')
    name=receipt.get('binary_name','')
    if Path(name).name!=name or not name.startswith('tebb-verify174-')or not name.endswith('.exe'):raise ValueError('验证程序文件名无效。')
    archive=installer.parent/f"tebb-{receipt['app_version']}-windows-x64.zip"
    hashes=archive_hashes(archive,f"tebb-{receipt['app_version']}/")
    if hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()!=receipt['payload_manifest_sha256']:
        raise ValueError('验证载荷摘要不一致。')
    if hashes.get('tebb.exe')!=receipt.get('binary_sha256'):raise ValueError('便携程序摘要不一致。')
    nsis_hash=receipt.get('nsis_binary_sha256','')
    if len(nsis_hash)!=64 or any(c not in '0123456789abcdef'for c in nsis_hash):raise ValueError('缺少NSIS实际输入摘要。')
    hashes['tebb.exe']=nsis_hash
    return receipt,hashes


def stopped(profile):
    profile=Path(profile)
    if profile.exists():
        with ResourceLock(RUNTIME_LOCK_NAME,directory=profile/'memory/locks').hold(blocking=False):pass


def operation_active(identifier):
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenMutexW.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.LPCWSTR];kernel.OpenMutexW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenMutexW(0x100000,False,installer_mutex_name(identifier))
    if not handle:
        code=ctypes.get_last_error()
        if code==2:return False
        raise ctypes.WinError(code)
    kernel.CloseHandle(handle);return True


def load_state(work):
    work=Path(work).resolve();state=json.loads((work/'state.json').read_text('utf-8'))
    if state.get('version')!=1 or state.get('verification')is not True:raise ValueError('缺少验证目录所有权。')
    for key,leaf in [('install_dir','安装目录 with spaces'),('profile','合成用户数据'),('webview','webview')]:
        path=Path(state[key])
        if path!=work/leaf or path.is_symlink()or getattr(path,'is_junction',lambda:False)():raise ValueError('验证目录越界。')
    return work,state


def check_files(state,hashes):
    install=Path(state['install_dir']);count=0
    for relative,expected in hashes.items():
        path=install/(state['binary_name']if relative=='tebb.exe'else relative)
        if not path.is_file()or digest(path)!=expected:raise ValueError('已安装文件与验证载荷不一致：'+relative)
        count+=1
    return count


def profile_snapshot(profile):
    stopped(profile)
    validate_tree(Path(profile),Path(profile).parent)
    return {p.relative_to(profile).as_posix():digest(p)for p in Path(profile).rglob('*')if p.is_file()and p.name!='backend.log'and 'locks'not in p.parts and '__pycache__'not in p.parts}


def finish_uninstall(state):
    pending=state['pending_uninstall'];install=Path(state['install_dir']);profile=Path(state['profile'])
    if pending.get('exit_code')not in(None,0):raise RuntimeError('原卸载器已返回失败，未重放。')
    deadline=time.monotonic()+60
    while True:
        remaining=[name for name in state['payload_hashes']if(install/(state['binary_name']if name=='tebb.exe'else name)).exists()]
        if(install/'uninstall.exe').exists():remaining.append('uninstall.exe')
        if not remaining:break
        if time.monotonic()>=deadline:raise RuntimeError('卸载仍待核对；剩余随包文件数：'+str(len(remaining)))
        time.sleep(.25)
    if pending['profile_before']!=profile_snapshot(profile):raise ValueError('默认卸载改变了合成用户数据。')
    state.pop('pending_uninstall')
    # The failed install's files are now demonstrably gone. Its immutable
    # failure report remains in the work directory; it cannot be reconciled
    # as a successful install after a subsequent fresh installation.
    state.pop('pending_install',None)
    return dict(data_preserved=True,program_removed=True,uninstaller_exit=pending.get('exit_code'),waited_for_actual_uninstaller=True,
        uninstaller_pid=pending['pid'],uninstaller_created=pending['created'],installer_probe_pid=pending.get('probe_pid'),installer_probe_exit=pending.get('probe_exit'))


def check_extension_fixture(state):
    install=Path(state['install_dir']);profile=Path(state['profile']);fixture=state['extension_fixture']
    environment={**os.environ,'DESK_COMPANION_DATA_DIR':str(profile)}
    status=json.loads(subprocess.check_output([str(install/'runtime/python.exe'),'-c','from desk_companion.runtime_extensions import main; main()','status'],env=environment,text=True,encoding='utf-8'))
    if not status['active']or status['compatibility']!=fixture['compatibility']or Path(status['path']).parent.name!=fixture['generation']:raise RuntimeError('扩展代未保留。')
    subprocess.run([str(install/'runtime/python.exe'),'-c',"import tebb_installer_fixture;assert tebb_installer_fixture.MARKER=='synthetic-extension-only'"],env=environment,check=True)
    return dict(synthetic_extension_preserved=True,pip_and_torch_verified=False)


def probe_background(state,work,installer=None,*,restored=False):
    """Exercise installed RPC restoration or actual NSIS backend protection."""
    if not restored:
        receipt,_=read_candidate(installer)
        if receipt['identifier']!=state['identifier']:raise ValueError('验证标识不一致。')
    elif 'data_fixtures'not in state:raise ValueError('须先用probe-background建立有回执的合成资料。')
    profile=Path(state['profile']);stopped(profile);install=Path(state['install_dir'])
    with socket.socket()as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    token=uuid.uuid4().hex;environment={**os.environ,'DESK_COMPANION_DATA_DIR':str(profile),'DESK_COMPANION_ASSET_DIR':str(profile/'assets')}
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));result={}
    checkpoint=work/('background-checkpoint-'+uuid.uuid4().hex[:8]+'.json')
    def save():checkpoint.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    with(work/('background-'+uuid.uuid4().hex[:8]+'.log')).open('w',encoding='utf-8')as log:
        process=subprocess.Popen([str(install/'runtime/python.exe'),'-u','-m','desk_companion.local_api.server','--port',str(port),'--token',token],cwd=install,env=environment,stdout=log,stderr=log,creationflags=0x08000000)
        result.update(backend_pid=process.pid,backend_created=psutil.Process(process.pid).create_time(),stage='starting');save()
        state['backend']=dict(pid=process.pid,created=result['backend_created'])
        (work/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        try:
            ready=False;deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                if process.poll()is not None:raise RuntimeError('验证后台提前退出。')
                try:
                    with opener.open(f'http://127.0.0.1:{port}/health',timeout=.5)as response:ready=response.status==200
                    if ready:break
                except OSError:pass
                time.sleep(.1)
            if not ready:raise RuntimeError('验证后台未就绪。')
            from websockets.asyncio.client import connect
            async def rpc(method,**args):
                async with connect(f'ws://127.0.0.1:{port}/ws?token={token}',proxy=None)as ws:
                    await ws.send(json.dumps(dict(type='rpc',id=uuid.uuid4().hex,method=method,args=args)))
                    envelope=json.loads(await asyncio.wait_for(ws.recv(),30))['result']
                    if not envelope['ok']or not envelope['result']['ok']:raise RuntimeError('合成资料接口未完成：'+method+'；'+str(envelope.get('error')or envelope.get('result',{}).get('error')or'未知'))
                    return envelope['result']
            async def seed():
                books=(await rpc('load_notebook'))['sessions']
                sid=next((book['id']for book in books if not book.get('notes')),None)or(await rpc('new_notebook'))['session_id']
                result.update(stage='notebook',notebook_session=sid);save()
                source=profile/'installer-fixture/source.txt';source.parent.mkdir(exist_ok=True)
                if not source.exists():source.write_text('安装升级验收用合成原文。',encoding='utf-8')
                staged=await rpc('stage_local_sources',paths=[str(source)],kind='file')
                source_id=staged['sources'][0]['id'];page=(await rpc('read_local_source',source_id=source_id))['source']
                note=await rpc('save_notebook_note',session_id=sid,question='安装升级合成问题',answer='安装升级合成笔记 [1]',cites=[dict(n=1,doc='安装升级合成来源',title='合成原文',text=page['text'],doc_id=source_id)])
                result.update(stage='calendar',note=note['note_id']);save()
                rid=uuid.uuid4().hex;result['calendar_request']=rid;save()
                start=(datetime.now(TZ)+timedelta(days=7)).replace(hour=9,minute=0,second=0,microsecond=0)
                event=await rpc('change_local_calendar',action=dict(type='create',request_id=rid,summary='安装升级合成日程',start=start.isoformat(),end=(start+timedelta(minutes=15)).isoformat(),item_id=''))
                return dict(notebook_session=sid,note=note['note_id'],calendar_saved=event['ok'],calendar_request=rid)
            if restored:
                from scripts.smoke_release import check
                saved=json.loads((profile/'onboarding.json').read_text('utf-8'))
                asyncio.run(check(port,0,profile,restored=True,token=token,expected_guide_show=not saved.get('completed')))
                async def inspect_data():
                    fixture=state['data_fixtures'];books=(await rpc('load_notebook'))['sessions']
                    if not any(b['id']==fixture['notebook_session']and any(n['id']==fixture['note']for n in b['notes'])for b in books):raise RuntimeError('已保存合成笔记未恢复。')
                    calendar=await rpc('load_local_calendar',request_id=fixture['calendar_request'])
                    if(calendar.get('operation')or{}).get('state')!='saved':raise RuntimeError('合成日程原回执未恢复。')
                asyncio.run(inspect_data());result.update(restored_data=True,model_requests=0)
                if state.get('extension_fixture'):result.update(check_extension_fixture(state))
            else:
                result['fixtures']=asyncio.run(seed());state['data_fixtures']=result['fixtures']
                (work/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
                result['stage']='installer_guard';save()
                command=subprocess.list2cmdline([str(Path(installer).resolve()),'/S','/NS'])+' /D='+str(install)
                attempted=subprocess.run(command,executable=str(Path(installer).resolve()),env=environment,timeout=60,check=False)
                result.update(installer_exit=attempted.returncode,backend_preserved=process.poll()is None)
                if attempted.returncode!=73 or not result['backend_preserved']:raise RuntimeError('仅后台运行时NSIS未按运行权拒绝。')
            result['verified_files']=check_files(state,state['payload_hashes'])
        finally:
            if process.poll()is None:
                request=urllib.request.Request(f'http://127.0.0.1:{port}/shutdown',headers={'Authorization':'Bearer '+token})
                try:
                    with opener.open(request,timeout=5)as response:result['shutdown_ack']=response.status==200
                except OSError as exc:
                    result['shutdown_error_kind']=type(exc).__name__;save();raise RuntimeError('后台退出请求未确认，原PID保留，未强停。')from exc
                try:result['backend_exit']=process.wait(timeout=120)
                except subprocess.TimeoutExpired:raise RuntimeError('后台仍在收尾，未强停。')
            else:result['backend_exit']=process.returncode
            save()
    if result['backend_exit']!=0:raise RuntimeError('后台未正常退出。')
    result['stage']='completed';save()
    return result


def action(args):
    if os.name!='nt':raise RuntimeError('需要Windows原生安装环境。')
    if not args.work.is_absolute():raise ValueError('验证工作目录须为绝对路径。')
    work=Path(args.work).absolute()
    if not work.is_absolute()or any(c in str(work)for c in ['"','\n','\r']):raise ValueError('验证工作目录无效。')
    if args.action=='install'and not work.exists():
        receipt,hashes=read_candidate(args.installer)
        work.mkdir(parents=True);state=dict(version=1,verification=True,identifier=receipt['identifier'],product_name=receipt['product_name'],binary_name=receipt['binary_name'],
            install_dir=str(work/'安装目录 with spaces'),profile=str(work/'合成用户数据'),webview=str(work/'webview'))
        (work/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    work,state=load_state(work);profile=Path(state['profile']);install=Path(state['install_dir']);result=dict(version=1,action=args.action,ok=False)
    if state.get('pending_uninstall')and args.action!='inspect-uninstall':raise RuntimeError('原卸载尚待核对，先执行inspect-uninstall，未再次执行。')
    if args.action in{'install','upgrade'}:
        receipt,hashes=read_candidate(args.installer)
        if receipt['identifier']!=state['identifier']or receipt['product_name']!=state['product_name']:raise ValueError('不能跨验证标识升级。')
        before=profile_snapshot(profile)if profile.exists()else{}
        # NSIS /D must be the last unquoted argument, including spaces. This
        # directly executes the EXE; no shell parses directory characters.
        command=subprocess.list2cmdline([str(Path(args.installer).resolve()),'/S','/NS'])+' /D='+str(install)
        env={**os.environ,'DESK_COMPANION_DATA_DIR':str(profile),'WEBVIEW2_USER_DATA_FOLDER':state['webview']}
        if state.get('pending_install'):raise RuntimeError('原安装仍待核对，未再次启动。先执行inspect。')
        process=subprocess.Popen(command,executable=str(Path(args.installer).resolve()),env=env)
        state['pending_install']=dict(pid=process.pid,created=psutil.Process(process.pid).create_time(),installer=str(Path(args.installer).resolve()),hashes=hashes,app_version=receipt['app_version'],profile_before=before)
        (work/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        try:code=process.wait(timeout=600)
        except subprocess.TimeoutExpired:raise RuntimeError('安装仍待核对；已记录原PID，未强停或再次安装。')
        result['installer_exit']=code;state['pending_install']['exit_code']=code
        (work/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        if code:raise RuntimeError('安装器未完成；请核对运行权及候选报告。')
        result['verified_files']=check_files(state,hashes)
        if profile.exists()and before!=profile_snapshot(profile):raise ValueError('安装期间合成用户数据发生变化。')
        state.pop('pending_install')
        state.update(app_version=receipt['app_version'],payload_hashes=hashes,uninstaller_sha256=digest(install/'uninstall.exe'))
    elif args.action=='snapshot':
        state['profile_snapshot']=profile_snapshot(profile);result['files']=len(state['profile_snapshot'])
    elif args.action=='seed-extension':
        stopped(profile)
        environment={**os.environ,'DESK_COMPANION_DATA_DIR':str(profile)}
        status=json.loads(subprocess.check_output([str(install/'runtime/python.exe'),'-c','from desk_companion.runtime_extensions import main; main()','status'],env=environment,text=True,encoding='utf-8'))
        if status['active']:raise RuntimeError('已有扩展，不覆盖原代。')
        generation=uuid.uuid4().hex[:16];layer=profile/'extensions'/status['compatibility'];target=layer/generation/'p';target.mkdir(parents=True)
        (target/'tebb_installer_fixture.py').write_text("MARKER='synthetic-extension-only'\n",encoding='utf-8')
        (target.parent/'result.json').write_text(json.dumps(dict(version=1,state='ready',compatibility=status['compatibility'],generation=generation,synthetic=True)),encoding='utf-8')
        with(layer/'current.json').open('x',encoding='utf-8')as stream:json.dump(dict(version=1,generation=generation),stream)
        state['extension_fixture']=dict(compatibility=status['compatibility'],generation=generation)
        result.update(synthetic=True,pip_and_torch_verified=False)
        completed=subprocess.run([str(install/'runtime/python.exe'),'-c',"import tebb_installer_fixture;assert tebb_installer_fixture.MARKER=='synthetic-extension-only';print('extension bootstrap OK')"],env=environment,capture_output=True,text=True,encoding='utf-8',check=True)
        result['bootstrap_verified']=completed.stdout.strip()=='extension bootstrap OK'
    elif args.action=='inspect':
        pending=state.get('pending_install')
        if pending:
            if pending.get('exit_code')not in(None,0):raise RuntimeError('原安装器已返回失败，保留原状态，不重放。')
            try:
                process=psutil.Process(pending['pid'])
                if process.create_time()==pending['created']:raise RuntimeError('原安装进程仍在运行，未再次启动。')
            except psutil.NoSuchProcess:pass
            result['verified_files']=check_files(state,pending['hashes'])
            if pending['profile_before']!=profile_snapshot(profile):raise ValueError('待核对安装期间用户数据发生变化。')
            state.update(payload_hashes=pending['hashes'],app_version=pending['app_version'],uninstaller_sha256=digest(install/'uninstall.exe'))
            state.pop('pending_install');result.update(reconciled_files=True,installer_exit=pending.get('exit_code'))
        result.update(verified_files=check_files(state,state['payload_hashes']),profile_exists=profile.exists())
    elif args.action=='launch':
        stopped(profile)
        env={**os.environ,'DESK_COMPANION_DATA_DIR':str(profile),'WEBVIEW2_USER_DATA_FOLDER':state['webview']}
        process=subprocess.Popen([str(install/state['binary_name'])],cwd=install,env=env)
        state['native']=dict(pid=process.pid,created=psutil.Process(process.pid).create_time());result['pid']=process.pid
    elif args.action=='probe-busy':
        receipt,hashes=read_candidate(args.installer)
        if receipt['identifier']!=state['identifier']:raise ValueError('验证标识不一致。')
        native=state['native'];parent=psutil.Process(native['pid'])
        if parent.create_time()!=native['created']or Path(parent.exe()).resolve()!=install/state['binary_name']:raise ValueError('原生进程身份已变化。')
        check_files(state,state['payload_hashes'])
        command=subprocess.list2cmdline([str(Path(args.installer).resolve()),'/S','/NS'])+' /D='+str(install)
        completed=subprocess.run(command,executable=str(Path(args.installer).resolve()),env={**os.environ,'DESK_COMPANION_DATA_DIR':str(profile)},timeout=60,check=False)
        result.update(installer_exit=completed.returncode,native_preserved=parent.is_running()and parent.create_time()==native['created'])
        if completed.returncode==0 or not result['native_preserved']:raise RuntimeError('运行中安装没有被正确阻止，保留失败记录。')
        result['verified_files']=check_files(state,state['payload_hashes'])
    elif args.action=='probe-native':
        native=state['native'];parent=psutil.Process(native['pid'])
        if parent.create_time()!=native['created']or Path(parent.exe()).resolve()!=install/state['binary_name']:raise ValueError('原生进程身份已变化。')
        deadline=time.monotonic()+30;backend=None
        while time.monotonic()<deadline:
            for process in parent.children(recursive=True):
                try:command=process.cmdline()
                except(psutil.NoSuchProcess,psutil.AccessDenied):continue
                if 'desk_companion.local_api.server'in command and '--port'in command and '--token'in command:backend=(process,command);break
            if backend:break
            time.sleep(.2)
        if not backend:raise RuntimeError('原生程序未拉起可核对的后台。')
        process,command=backend;port=int(command[command.index('--port')+1]);token=command[command.index('--token')+1]
        state['backend']=dict(pid=process.pid,created=process.create_time())
        (work/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));ready=False;deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            try:
                with opener.open(f'http://127.0.0.1:{port}/health',timeout=.5)as response:ready=response.status==200
                if ready:break
            except OSError:pass
            time.sleep(.1)
        if not ready:raise RuntimeError('原生后台尚未就绪，未启动假模型请求。')
        from http.server import ThreadingHTTPServer
        from scripts.smoke_release import ModelServer,check
        ModelServer.requests=[];server=ThreadingHTTPServer(('127.0.0.1',0),ModelServer);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        saved_guide=json.loads((profile/'onboarding.json').read_text('utf-8'))if(profile/'onboarding.json').is_file()else{}
        expected_guide_show=bool(saved_guide)and not saved_guide.get('completed')
        try:asyncio.run(check(port,server.server_port,profile,restored=args.restored,token=token,expected_guide_show=expected_guide_show))
        finally:server.shutdown();server.server_close();thread.join(5)
        result.update(backend_pid=process.pid,fake_model_requests=len(ModelServer.requests),native_parent_verified=True)
        if state.get('extension_fixture'):
            result.update(check_extension_fixture(state))
        state['backend']=dict(pid=process.pid,created=process.create_time())
    elif args.action=='probe-background':
        result.update(probe_background(state,work,args.installer))
    elif args.action=='probe-restored':
        result.update(probe_background(state,work,restored=True))
    elif args.action=='uninstall':
        stopped(profile);uninstall=install/'uninstall.exe'
        if digest(uninstall)!=state['uninstaller_sha256']:raise ValueError('卸载器与原验证安装不一致。')
        if args.installer:
            receipt,_=read_candidate(args.installer)
            if receipt['identifier']!=state['identifier']:raise ValueError('并发安装验证标识不一致。')
        pending=state.get('pending_install')
        if pending:
            if pending.get('exit_code')is None:raise RuntimeError('原安装退出状态未知，先核对原安装，不并行卸载。')
            try:
                original=psutil.Process(pending['pid'])
                if original.create_time()==pending['created']:raise RuntimeError('原安装仍在运行，不并行卸载。')
            except psutil.NoSuchProcess:pass
        before=profile_snapshot(profile)
        # NSIS normally forks a temporary copy and immediately returns. Follow
        # its documented automation contract: run a verified copy outside the
        # installation with _?= last, so wait() observes the actual uninstaller.
        copied=work/('uninstaller-'+uuid.uuid4().hex+'.exe')
        with copied.open('xb')as stream:stream.write(uninstall.read_bytes())
        if digest(copied)!=state['uninstaller_sha256']:raise ValueError('验证卸载副本摘要不一致。')
        command=subprocess.list2cmdline([str(copied),'/S'])+' _?='+str(install)
        process=subprocess.Popen(command,executable=str(copied),env={**os.environ,'DESK_COMPANION_DATA_DIR':str(profile)})
        state['pending_uninstall']=dict(pid=process.pid,created=psutil.Process(process.pid).create_time(),profile_before=before,executable=str(copied),sha256=state['uninstaller_sha256'])
        (work/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        if args.installer:
            deadline=time.monotonic()+20
            while not operation_active(state['identifier']):
                if process.poll()is not None or time.monotonic()>deadline:raise RuntimeError('未观察到实际卸载进程持有安装互斥量。')
                time.sleep(.1)
            command=subprocess.list2cmdline([str(Path(args.installer).resolve()),'/S','/NS'])+' /D='+str(install)
            attempt=subprocess.Popen(command,executable=str(Path(args.installer).resolve()),env={**os.environ,'DESK_COMPANION_DATA_DIR':str(profile)})
            pending=state['pending_uninstall'];pending.update(probe_pid=attempt.pid,probe_created=psutil.Process(attempt.pid).create_time())
            (work/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
            try:pending['probe_exit']=attempt.wait(timeout=60)
            except subprocess.TimeoutExpired:raise RuntimeError('并发安装拒绝尚待核对，保留原PID，不强停或重放。')
            (work/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
            if pending['probe_exit']!=73:raise RuntimeError('实际卸载运行时并发安装未被拒绝。')
            result['installer_refused_during_uninstall']=True
        try:state['pending_uninstall']['exit_code']=process.wait(timeout=600)
        except subprocess.TimeoutExpired:raise RuntimeError('原卸载仍在运行，已记录原PID，未强停或重放。')
        (work/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        result.update(finish_uninstall(state))
    elif args.action=='inspect-uninstall':
        pending=state.get('pending_uninstall')
        if not pending:raise RuntimeError('没有待核对的卸载。')
        try:
            process=psutil.Process(pending['pid'])
            if process.create_time()==pending['created']:raise RuntimeError('原卸载进程仍在运行，未再次执行。')
        except psutil.NoSuchProcess:pass
        result.update(finish_uninstall(state))
    elif args.action=='check-exited':
        alive=[]
        for key in('native','backend'):
            saved=state.get(key)
            if not saved:continue
            try:
                process=psutil.Process(saved['pid'])
                if process.create_time()==saved['created']:alive.append(key)
            except psutil.NoSuchProcess:pass
        result.update(alive=alive)
        if alive:raise RuntimeError('自建原生/后台进程尚未退出，未声称完成收尾。')
    result['ok']=True
    (work/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    return work,result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['install','upgrade','snapshot','seed-extension','inspect','launch','probe-busy','probe-native','probe-background','probe-restored','uninstall','inspect-uninstall','check-exited'])
    parser.add_argument('--work',type=Path,required=True);parser.add_argument('--installer',type=Path);parser.add_argument('--restored',action='store_true');args=parser.parse_args()
    work=Path(args.work).absolute()
    try:work,result=action(args)
    except Exception as exc:
        frame=traceback.extract_tb(exc.__traceback__)[-1]
        result=dict(version=1,action=args.action,ok=False,error_kind=type(exc).__name__,error=str(exc),failure_step=Path(frame.filename).name+':'+str(frame.lineno))
    if work.is_dir():
        report=work/(args.action+'-'+uuid.uuid4().hex[:10]+'.json')
        with report.open('x',encoding='utf-8')as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False));raise SystemExit(0 if result['ok']else 1)


if __name__=='__main__':main()
