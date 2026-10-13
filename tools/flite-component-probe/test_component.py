#!/usr/bin/env python3
"""Offline fixtures only; never executes the proposed native artifact or SDK."""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import component_probe as c

class Guards(unittest.TestCase):
    def env(self):
        return dict(PROBE_REPOSITORY_PRIVATE='false', PROBE_REPOSITORY_VISIBILITY='public',
                    GITHUB_REPOSITORY='Hatsunama/sign-to-speak', GITHUB_ACTIONS='true',
                    RUNNER_ENVIRONMENT='github-hosted', GITHUB_EVENT_NAME='push', GITHUB_REF=c.BRANCH,
                    ImageOS='ubuntu24', RUNNER_OS='Linux', RUNNER_ARCH='X64', PROBE_COMMIT='a'*40,
                    PROBE_CELL='api26')
    def test_policy_fail_closed(self):
        c.policy(self.env())
        for key in self.env():
            env=self.env();env[key]='unexpected'
            with self.subTest(key=key),self.assertRaises(c.base.Blocked):c.policy(env)
        env=self.env();env['PROBE_CELL']='api35-16k';c.policy(env)
    def elf(self):
        data=bytearray(1024);data[:7]=b'\x7fELF\x02\x01\x01'
        struct.pack_into('<HH',data,16,3,62);struct.pack_into('<QQ',data,24,512,64)
        struct.pack_into('<HH',data,54,56,2)
        struct.pack_into('<IIQQQQQQ',data,64,1,5,0,0,0,1024,1024,16384)
        interp=b'/system/bin/linker64\0';data[512:512+len(interp)]=interp
        struct.pack_into('<IIQQQQQQ',data,120,3,4,512,512,512,len(interp),len(interp),1)
        return bytes(data)
    def test_elf_architecture_alignment_and_interpreter(self):
        c.verify_elf(self.elf())
        fixtures=[]
        for offset,fmt,value in [(18,'<H',183),(64+48,'<Q',4096),(64+4,'<I',7),(54,'<H',55)]:
            b=bytearray(self.elf());struct.pack_into(fmt,b,offset,value);fixtures.append(bytes(b))
        fixtures += [b'not-elf',self.elf().replace(b'linker64',b'badxxxxx')]
        for data in fixtures:
            with self.assertRaises(c.base.Blocked):c.verify_elf(data)
    def artifacts(self,root):
        identity={}
        for role,relative,body in [('binary',c.BINARY_RELATIVE,self.elf()),('source',c.SOURCE_RELATIVE,b'fixture source'),
                                   ('notices',c.NOTICES_RELATIVE,b'fixture notices'),('evidence',c.EVIDENCE_RELATIVE,b'fixture evidence')]:
            p=root/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(body)
            identity[role+'_sha256']=hashlib.sha256(body).hexdigest();identity[role+'_size']=len(body)
        return identity
    def test_pending_identity_cannot_be_enabled_from_environment(self):
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{'REVIEWED_IDENTITY':'approved'}), patch.object(c,'REVIEWED_IDENTITY',None):
            with self.assertRaisesRegex(c.base.Blocked,'FINAL_REVIEWED_ARTIFACT_IDENTITY_REQUIRED'):
                c.verify_artifacts(Path(td))
    def test_exact_binary_source_notices_and_evidence_binding(self):
        for role,relative in [('binary',c.BINARY_RELATIVE),('source',c.SOURCE_RELATIVE),('notices',c.NOTICES_RELATIVE),('evidence',c.EVIDENCE_RELATIVE)]:
            with self.subTest(role=role),tempfile.TemporaryDirectory() as td:
                root=Path(td);identity=self.artifacts(root);c.verify_artifacts(root,identity)
                (root/relative).write_bytes(b'changed')
                with self.assertRaisesRegex(c.base.Blocked,'MISMATCH'):c.verify_artifacts(root,identity)
    def test_artifact_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);identity=self.artifacts(root);path=root/c.BINARY_RELATIVE
            other=root/'outside';other.write_bytes(path.read_bytes());path.unlink();path.symlink_to(other)
            with self.assertRaisesRegex(c.base.Blocked,'REGULAR_FILE'):c.verify_artifacts(root,identity)
    def test_artifact_symlink_ancestor_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);identity=self.artifacts(root)
            parent=(root/c.BINARY_RELATIVE).parent
            target=parent.with_name('relocated-artifacts');parent.rename(target);parent.symlink_to(target)
            with self.assertRaisesRegex(c.base.Blocked,'REGULAR_FILE'):c.verify_artifacts(root,identity)

    def test_client_timeout_owned_guest_survives_until_cleanup_then_is_destroyed(self):
        # Separate owned Python process models the emulator whose guest-side CLI
        # may outlive a timed-out adb client. No emulator or native binary runs.
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=self.instance(root);p.work=root/'owned-guest';p.work.mkdir()
            (p.work/'guest-data').write_text('synthetic fixture')
            guest,_=p.spawn([sys.executable,'-c','import time;time.sleep(60)'],'guest-fixture.txt')
            try:
                def timeout():
                    p.command([sys.executable,'-c','import time;time.sleep(60)'],'adb-client-fixture.txt',seconds=.05)
                with patch.object(c.base.Probe,'execute'),patch.object(p,'execute_component',side_effect=timeout):
                    with self.assertRaisesRegex(c.base.Blocked,'TIMEOUT'):p.execute()
                self.assertIsNone(guest.poll())
                self.assertEqual(p.result['environment_status'],'verified')
                self.assertEqual(p.result['component_status'],'failed')
            finally:
                self.assertTrue(p.cleanup())
            self.assertIsNotNone(guest.poll());self.assertFalse(p.children)
            self.assertFalse(p.work.exists());self.assertEqual(p.result['cleanup_errors'],[])

    def test_nonempty_pcm_with_wrong_reference_is_rejected(self):
        for key,value in [('samples',43039),('pcm_fnv1a64_le','0'*16)]:
            data=self.metrics();data[key]=value
            with self.assertRaisesRegex(c.base.Blocked,'REFERENCE_MISMATCH'):
                c.validate_metrics(json.dumps(data),4096)

    def metrics(self,page=4096):
        return dict(status='ok',sample_rate=16000,channels=1,encoding='signed_pcm16',samples=c.EXPECTED_PCM_SAMPLES,
                    pcm_fnv1a64_le=c.EXPECTED_PCM_FNV1A64_LE,latency_ms=123.4,owner_cleared=True,
                    empty_rejected=True,pre_canceled_rejected=True,page_size=page)
    def test_metrics_success_and_exact_runtime_page_size(self):
        for page in (4096,16384):c.validate_metrics(json.dumps(self.metrics(page)),page)
        with self.assertRaisesRegex(c.base.Blocked,'page_size'):c.validate_metrics(json.dumps(self.metrics()),16384)
    def test_bad_metric_types_ranges_flags_and_extra_pcm_rejected(self):
        for key,value in [('samples',0),('samples',480001),('samples',True),('sample_rate',True),
                          ('channels',2),('latency_ms',float('nan')),('latency_ms',float('inf')),
                          ('latency_ms',True),('latency_ms',60000),('owner_cleared',False),
                          ('pcm_fnv1a64_le','raw-PCM'),('pcm',[1,2,3]),('page_size','4096')]:
            with self.subTest(key=key,value=value):
                data=self.metrics();data[key]=value
                with self.assertRaises(c.base.Blocked):c.validate_metrics(json.dumps(data),4096)
    def test_duplicate_multiline_or_binary_output_rejected(self):
        for value in ('{"status":"ok","status":"bad"}',json.dumps(self.metrics())+'\nextra', '[1,2]', '\x00data'):
            with self.assertRaises(c.base.Blocked):c.validate_metrics(value,4096)
    def instance(self,root):return c.ComponentProbe(root,root,root/'never-executed',{})
    def test_component_failure_cannot_inherit_environment_success(self):
        with tempfile.TemporaryDirectory() as td:
            p=self.instance(Path(td))
            with patch.object(c.base.Probe,'execute'),patch.object(p,'execute_component',side_effect=c.base.Blocked('fixture')):
                with self.assertRaises(c.base.Blocked):p.execute()
            self.assertEqual(p.result['environment_status'],'verified')
            self.assertEqual(p.result['component_status'],'failed')
            self.assertEqual(p.result['status'],'native-component-failed')
    def test_component_commands_bounded_and_children_cleaned(self):
        with tempfile.TemporaryDirectory() as td:
            p=self.instance(Path(td))
            self.assertEqual(p.command([sys.executable,'-c','print("fixture")'],'ok.txt'),'fixture')
            with self.assertRaisesRegex(c.base.Blocked,'TIMEOUT'):
                p.command([sys.executable,'-c','import time;time.sleep(60)'],'slow.txt',seconds=.05)
            self.assertFalse(p.children)
            with self.assertRaisesRegex(c.base.Blocked,'OUTPUT_LIMIT|LOG_LIMIT'):
                p.command([sys.executable,'-c','print("x" * 10000)'],'large.txt')
            self.assertFalse(p.children)
            with self.assertRaisesRegex(c.base.Blocked,'FAILED'):
                p.command([sys.executable,'-c','raise SystemExit(7)'],'failed.txt')
            p.cleanup()
    def test_pending_preflight_cannot_construct_runner_or_touch_sdk(self):
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,self.env(),clear=True), \
             patch.object(c.subprocess,'check_output',side_effect=['a'*40+'\n',c.PARENT+'\n']), \
             patch.object(c,'ComponentProbe') as constructor, patch.object(c,'REVIEWED_IDENTITY',None):
            with self.assertRaisesRegex(c.base.Blocked,'FINAL_REVIEWED_ARTIFACT_IDENTITY_REQUIRED'):
                c.main()
            constructor.assert_not_called()

    def test_component_transfer_own_serial_hash_metrics_and_removal(self):
        for cell,page in [('api26',4096),('api35-16k',16384)]:
            with self.subTest(cell=cell),tempfile.TemporaryDirectory() as td:
                root=Path(td);binary=root/'fixture-elf';binary.write_bytes(self.elf())
                digest=hashlib.sha256(binary.read_bytes()).hexdigest()
                p=c.ComponentProbe(root,root,binary,{'binary_sha256':digest})
                p.sdk=root/'sdk';c.base.select_cell(cell)
                facts=dict(boot_completed='1',page_size=str(page),sdk=c.base.EXPECTED_SDK,
                           abi='x86_64',abilist='x86_64',fingerprint='fixture')
                (root/'runtime-facts.json').write_text(json.dumps(facts))
                calls=[]
                remote='/data/local/tmp/sign-flite-'+digest[:16]+'/private_tts_cli_probe'
                def command(args,name,**kwargs):
                    calls.append(args)
                    self.assertEqual(args[1:5],['-P','5038','-s','emulator-5556'])
                    if name=='component-device-state.txt':return 'device'
                    if name=='component-guest-hash.txt':return digest+'  '+remote
                    if name=='component-raw-output.txt':return json.dumps(self.metrics(page))
                    return ''
                with patch.object(c.base,'verify_loopback_listener'),patch.object(p,'command',side_effect=command):
                    p.execute_component()
                self.assertTrue(p.result['component_evidence']['guest_binary_removed'])
                self.assertEqual(sum('sha256sum' in x for x in calls),2)
                self.assertTrue(any(x[-3:]==['shell','rm',remote] for x in calls))
                self.assertFalse(any('install' in x for x in calls))

    def test_wrong_guest_hash_blocks_native_execution(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);binary=root/'fixture-elf';binary.write_bytes(self.elf())
            digest=hashlib.sha256(binary.read_bytes()).hexdigest()
            p=c.ComponentProbe(root,root,binary,{'binary_sha256':digest});p.sdk=root/'sdk'
            c.base.select_cell('api26')
            (root/'runtime-facts.json').write_text(json.dumps(dict(boot_completed='1',page_size='4096',
                sdk='26',abi='x86_64',abilist='x86_64',fingerprint='fixture')))
            calls=[]
            def command(args,name,**kwargs):
                calls.append(name)
                return 'device' if name=='component-device-state.txt' else 'wrong-hash'
            with patch.object(c.base,'verify_loopback_listener'),patch.object(p,'command',side_effect=command), \
                 self.assertRaisesRegex(c.base.Blocked,'GUEST_HASH_MISMATCH'):
                p.execute_component()
            self.assertNotIn('component-raw-output.txt',calls)
            self.assertNotIn('component-mode.txt',calls)

    def test_post_transfer_or_post_execution_hash_and_path_mismatch_fail_closed(self):
        for bad_index in (1,2):
            for wrong_path in (False,True):
                with self.subTest(hash_check=bad_index,wrong_path=wrong_path),tempfile.TemporaryDirectory() as td:
                    root=Path(td);binary=root/'fixture-elf';binary.write_bytes(self.elf())
                    digest=hashlib.sha256(binary.read_bytes()).hexdigest()
                    p=c.ComponentProbe(root,root,binary,{'binary_sha256':digest});p.sdk=root/'sdk'
                    c.base.select_cell('api26')
                    (root/'runtime-facts.json').write_text(json.dumps(dict(boot_completed='1',page_size='4096',
                        sdk='26',abi='x86_64',abilist='x86_64',fingerprint='fixture')))
                    remote='/data/local/tmp/sign-flite-'+digest[:16]+'/private_tts_cli_probe'
                    checks=[];calls=[]
                    def command(args,name,**kwargs):
                        calls.append(name)
                        if name=='component-device-state.txt':return 'device'
                        if name=='component-raw-output.txt':return json.dumps(self.metrics())
                        if name=='component-guest-hash.txt':
                            checks.append(name)
                            if len(checks)==bad_index:
                                return digest+'  '+remote+'-other' if wrong_path else '0'*64+'  '+remote
                            return digest+'  '+remote
                        return ''
                    with patch.object(c.base.Probe,'execute'),patch.object(c.base,'verify_loopback_listener'), \
                         patch.object(p,'command',side_effect=command):
                        with self.assertRaisesRegex(c.base.Blocked,'GUEST_HASH_MISMATCH'):p.execute()
                    self.assertEqual(p.result['component_status'],'failed')
                    self.assertNotIn('component_evidence',p.result)
                    self.assertEqual('component-raw-output.txt' in calls,bad_index==2)
                    self.assertTrue(p.cleanup())

    def test_workflow_is_two_cell_public_only_and_identity_before_install(self):
        root=Path(__file__).resolve().parents[2]
        wf=(root/'.github/workflows/sign-flite-component-probe.yml').read_text()
        for phrase in ('runs-on: ubuntu-24.04','cell: [api26, api35-16k]','max-parallel: 1',
                       "github.event.repository.private == false",'persist-credentials: false','timeout-minutes: 25'):
            self.assertIn(phrase,wf)
        self.assertLess(wf.index('component_probe.py --preflight'),wf.index('sudo -n apt-get'))
        for phrase in ('upload-artifact@','cache@','secrets.','id-token:','workflow_dispatch:','pull_request:'):
            self.assertNotIn(phrase,wf)
        self.assertEqual(c.SERIAL,'emulator-5556');self.assertEqual(c.ADB_PORT,'5038')
        source=Path(c.__file__).read_text()
        for phrase in ('adb install','keytool','apksigner','-a\'', 'start-server', 'pkill', 'setenforce'):
            self.assertNotIn(phrase,source)

if __name__=='__main__':unittest.main(verbosity=2)
