"""Official CLI boundaries without invoking Docker, a model or a real sandbox."""
import json
import os
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desk_companion.docker_sandboxes import SandboxesClient,SandboxError,SHELL_KIT,DEPENDENCY_HOSTS


class CLI:
    def __init__(self,responses):self.responses=iter(responses);self.calls=[]
    def __call__(self,command,**kwargs):
        self.calls.append((command,kwargs))
        value=next(self.responses)
        if isinstance(value,Exception):raise value
        return SimpleNamespace(returncode=0,stdout=value,stderr='')


def inventory(state='running',ident='id-1'):
    return json.dumps([dict(name='project-1',id=ident,status=state),dict(name='unrelated',id='other',status='running')])


class SandboxesClientTests(unittest.TestCase):
    def test_failing_recorder_cannot_retry_or_replace_actual_cli_result(self):
        calls=[]
        def runner(command,**kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=17,stdout='',stderr='synthetic private detail')
        client=SandboxesClient(program='sbx-test',runner=runner)
        client.observe_control(lambda *a,**k:(_ for _ in ()).throw(OSError('synthetic full log')))
        with self.assertRaises(SandboxError)as failure:client._call(['create'],mutating=True)
        self.assertEqual(len(calls),1)
        self.assertEqual(failure.exception.exit_code,17)
        self.assertTrue(failure.exception.outcome_unknown)
        self.assertFalse(failure.exception.details['diagnostics_available'])
        self.assertNotIn('synthetic private detail',str(failure.exception))
        client.runner=lambda *a,**k:SimpleNamespace(returncode=0,stdout='actual result',stderr='')
        self.assertEqual(client._call(['create'],mutating=True),'actual result')

    def test_cli_does_not_inherit_product_rpc_or_common_provider_keys(self):
        with patch.dict(os.environ,DESK_TERMINAL_TOKEN='synthetic-rpc-secret',OPENAI_API_KEY='synthetic-key',LOCALAPPDATA='user-login-location'):
            client,runner=self.client(['sbx version v0.50.1','[]']);self.assertTrue(client.probe()['ok'])
        for _,kwargs in runner.calls:
            self.assertNotIn('DESK_TERMINAL_TOKEN',kwargs['env']);self.assertNotIn('OPENAI_API_KEY',kwargs['env'])
            self.assertEqual(kwargs['env']['LOCALAPPDATA'],'user-login-location')
    def test_dependency_grants_are_scoped_and_effective_denies_are_not_bypassed(self):
        for allowed in (True,False):
            responses=[inventory(),'granted']+[json.dumps(dict(allowed=allowed,context='sandbox:project-1')) for host in DEPENDENCY_HOSTS]
            client,runner=self.client(responses)
            if allowed:self.assertFalse(client.dependency_network('project-1','id-1')['exclusive_allowlist'])
            else:
                with self.assertRaises(SandboxError) as failure:client.dependency_network('project-1','id-1')
                self.assertEqual(failure.exception.code,'network_denied')
            for command,_ in runner.calls[1:]:
                self.assertIn('--sandbox',command);self.assertEqual(command[command.index('--sandbox')+1],'project-1')

    def test_probe_parses_actual_official_prefixed_version(self):
        client,runner=self.client(['sbx version v0.50.1 ddb0268d7124b9cf98955d0b08ffd71a0dfcebe4','[]'])
        self.assertEqual(client.probe(),dict(ok=True,provider='docker_sandboxes',version='0.50.1',scope='control_plane',guest_verified=False))

    def client(self,responses):
        runner=CLI(responses)
        return SandboxesClient(program='sbx-test',runner=runner),runner

    def test_create_has_no_cloud_host_workspace_or_credential_forwarding_flags(self):
        client,runner=self.client(['[]','created',inventory()])
        created=client.create('project-1','a'*32)
        self.assertEqual(created['id'],'id-1')
        command=runner.calls[1][0]
        self.assertEqual(command[-1],SHELL_KIT)
        self.assertEqual(command[1:4],['create','--name','project-1'])
        self.assertEqual(command[command.index('--skills')+1],'off')
        self.assertEqual(command[command.index('--env')+1],'TEBB_ENV_ID='+'a'*32)
        self.assertNotIn('--mount',command);self.assertNotIn('--cloud',command)
        self.assertNotIn('shell',command)

    def test_unknown_create_is_not_retried_or_replaced(self):
        client,runner=self.client(['[]',subprocess.TimeoutExpired('create',1)])
        with self.assertRaises(SandboxError) as failure:client.create('project-1','a'*32)
        self.assertTrue(failure.exception.outcome_unknown)
        self.assertEqual(failure.exception.code,'outcome_unknown')
        self.assertEqual(len(runner.calls),2)

    def test_create_ack_without_observed_identity_remains_unknown(self):
        client,runner=self.client(['[]','created','[]'])
        with self.assertRaises(SandboxError) as failure:client.create('project-1','a'*32)
        self.assertTrue(failure.exception.outcome_unknown)
        self.assertEqual(len(runner.calls),3)

    def test_same_name_different_identity_cannot_stop_or_execute(self):
        client,runner=self.client([inventory(ident='replacement')])
        with self.assertRaises(SandboxError) as failure:client.stop('project-1','original')
        self.assertEqual(failure.exception.code,'identity_changed')
        self.assertEqual(len(runner.calls),1)

    def test_exec_stays_in_official_cli_for_existing_supervisor(self):
        client,runner=self.client([inventory()])
        command=client.exec_command('project-1','id-1',['bash','-lc','echo guest; touch /tmp/example'],interactive=True)
        self.assertEqual(command,['sbx-test','exec','-i','project-1','bash','-lc','echo guest; touch /tmp/example'])
        self.assertEqual(len(runner.calls),1)  # no unbounded user-command capture in this adapter

    def test_published_ports_are_explicit_ipv4_loopback(self):
        client,runner=self.client([inventory(),'published'])
        result=client.publish('project-1','id-1',3000,18901)
        self.assertEqual(runner.calls[-1][0][-1],'127.0.0.1:18901:3000/tcp4')
        self.assertEqual(result['host'],'127.0.0.1')

    def test_invalid_port_and_name_do_not_reach_mutating_commands(self):
        client,runner=self.client([inventory()])
        with self.assertRaises(ValueError):client.publish('project-1','id-1',True,18901)
        self.assertEqual(len(runner.calls),1)
        with self.assertRaises(ValueError):client.create('../host','a'*32)
        self.assertEqual(len(runner.calls),1)

    def test_stop_checks_actual_state_and_does_not_remove_environment(self):
        client,runner=self.client([inventory(),'stopped',inventory('stopped')])
        self.assertEqual(client.stop('project-1','id-1')['state'],'stopped')
        self.assertEqual([c[0][1] for c in runner.calls],['ls','stop','ls'])

    def test_probe_does_not_claim_guest_isolation_from_installation(self):
        client,runner=self.client(['Docker Sandboxes version 0.50.1','[]'])
        result=client.probe()
        self.assertTrue(result['ok']);self.assertFalse(result['guest_verified'])
        self.assertEqual(result['scope'],'control_plane')
        self.assertEqual([c[0][1] for c in runner.calls],['version','ls'])

    def test_missing_cli_fails_closed_without_host_fallback(self):
        with patch('shutil.which',return_value=None),patch('pathlib.Path.is_file',return_value=False):
            client=SandboxesClient(runner=lambda *a,**k:self.fail('must not launch any process'))
        self.assertEqual(client.probe()['code'],'not_installed')
