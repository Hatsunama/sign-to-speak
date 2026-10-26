import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).with_name('run-phone-recognition-probe.py')
spec = importlib.util.spec_from_file_location('probe_runner', MODULE)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

APP = 'com.xmilo_at_your_side.sign_to_speak.qa'
HEX = 'a' * 64


class FakeAdb:
    def __init__(self, *, foreground='com.sec.android.app.launcher/.Launcher', busy=False,
                 unknown=False, installed=HEX, bad_stage=False, instrument='OK (1 test)',
                 timeout_instrument=False, other_app_on_cleanup=False, result_clip_id='clip1',
                 other_app_after_cleanup=False, cleanup_result='OK (1 test)'):
        self.calls = []
        self.foreground, self.busy, self.unknown = foreground, busy, unknown
        self.installed, self.bad_stage, self.instrument = installed, bad_stage, instrument
        self.timeout_instrument = timeout_instrument
        self.other_app_on_cleanup = other_app_on_cleanup
        self.instrument_seen = False
        self.result_clip_id = result_clip_id
        self.other_app_after_cleanup = other_app_after_cleanup
        self.cleanup_result = cleanup_result
        self.cleanup_seen = False

    def __call__(self, args, timeout):
        self.calls.append(args)
        command = ' '.join(args)
        if 'dumpsys activity activities' in command:
            takeover = ((self.other_app_on_cleanup and self.instrument_seen)
                        or (self.other_app_after_cleanup and self.cleanup_seen))
            fg = 'com.other/.Main' if takeover else self.foreground
            return (0, 'topResumedActivity=ActivityRecord{' + fg + '}', '')
        if 'dumpsys activity processes' in command:
            return (0, 'Unknown command' if self.unknown else
                    'ACTIVITY MANAGER RUNNING PROCESSES\n' + ('Active instrumentation:' if self.busy else ''), '')
        if 'dumpsys window policy' in command:
            return (0, 'inputRestricted=false showing=false', '')
        if 'pm path ' in command:
            return (0, 'package:/data/app/owned/base.apk', '')
        if 'sha256sum /data/app' in command:
            return (0, self.installed + '  /data/app/owned/base.apk', '')
        if 'sha256sum /data/local/tmp/' in command:
            name = args[-1].rsplit('/', 1)[-1]
            expected = self.stage_hashes.get(name, self.expected_stage)
            return (0, ('0' * 64 if self.bad_stage else expected) + '  staged', '')
        if 'am instrument' in command:
            if '#cleanupStagedInputAfterInterruptedProbe' in command:
                self.cleanup_seen = True
                return (0, self.cleanup_result, '')
            if '#recordedVideoToHolisticToLocalModel' in command or '#stagedInputHandoffIsReadable' in command:
                self.instrument_seen = True
                if self.timeout_instrument:
                    raise runner.ProbeError('ADB unavailable or timed out')
            return (0, self.instrument, '')
        if 'pull' in args:
            Path(args[-1]).write_text(json.dumps({'schema_version': 1,
                'manifest_sha256': self.expected_stage,
                'clips': [{'clip_id': self.result_clip_id}]}), encoding='utf-8')
        return (0, '', '')


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = self.root / 'manifest.json'
        self.manifest.write_text('{}', encoding='utf-8')
        self.receipt = self.root / 'receipt.json'
        self.receipt.write_text(json.dumps({'source_sha': 'b' * 40, 'artifacts': [
            {'role': 'app', 'signed_sha256': HEX}, {'role': 'test', 'signed_sha256': HEX}]}))
        self.kw = dict(serial='SER123', mode='diagnostic', manifest=self.manifest,
                       receipt=self.receipt, expected_source='b' * 40,
                       output=self.root / 'fresh', token='1' * 32, workspace_root=self.root)

    def fake(self, **options):
        fake = FakeAdb(**options)
        fake.expected_stage = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        fake.stage_hashes = {}
        return fake

    def test_busy_or_unknown_instrumentation_has_no_mutations(self):
        for options in ({'busy': True}, {'unknown': True}, {'foreground': 'com.other/.Main'}):
            with self.subTest(options=options):
                fake = self.fake(**options)
                with self.assertRaises(runner.ProbeError):
                    runner.run_probe(**self.kw, adb=fake)
                self.assertFalse(any('push' in call or 'mkdir' in call or 'instrument' in call
                                     for call in fake.calls))

    def test_wrong_installed_hash_fails_before_stage(self):
        fake = self.fake(installed='0' * 64)
        with self.assertRaises(runner.ProbeError):
            runner.run_probe(**self.kw, adb=fake)
        self.assertFalse(any('push' in call for call in fake.calls))

    def test_stage_hash_failure_cleans_only_owned_path(self):
        fake = self.fake(bad_stage=True)
        with self.assertRaises(runner.ProbeError):
            runner.run_probe(**self.kw, adb=fake)
        calls = [' '.join(c) for c in fake.calls]
        self.assertTrue(any('rm -f /data/local/tmp/sign-recognition-' in c for c in calls))
        self.assertFalse(any('rm -r' in c for c in calls))

    def test_diagnostic_selects_one_method_and_succeeds(self):
        fake = self.fake()
        report = runner.run_probe(**self.kw, adb=fake)
        calls = [' '.join(c) for c in fake.calls]
        selected = next(c for c in calls if 'am instrument' in c)
        self.assertIn('#stagedInputHandoffIsReadable', selected)
        self.assertIn('recognitionInputProbe true', selected)
        self.assertNotIn('recognitionProbe true', selected)
        self.assertEqual(report['test_count'], 1)
        self.assertTrue(report['passed'])
        self.assertTrue(report['app_private_cleanup_passed'])
        self.assertFalse(report['cleanup_pending'])

    def test_instrumentation_failure_cannot_pass(self):
        fake = self.fake(instrument='FAILURES!!!\nTests run: 1,  Failures: 1')
        with self.assertRaises(runner.ProbeError):
            runner.run_probe(**self.kw, adb=fake)
        self.assertTrue(any('rmdir /data/local/tmp/sign-recognition-' in ' '.join(c)
                            for c in fake.calls))
        self.assertTrue(any('#cleanupStagedInputAfterInterruptedProbe' in ' '.join(c)
                            for c in fake.calls))

    def test_timeout_runs_private_cleanup(self):
        fake = self.fake(timeout_instrument=True)
        with self.assertRaises(runner.ProbeError):
            runner.run_probe(**self.kw, adb=fake)
        self.assertTrue(any('#cleanupStagedInputAfterInterruptedProbe' in ' '.join(c)
                            for c in fake.calls))

    def test_other_app_after_instrument_leaves_cleanup_pending(self):
        fake = self.fake(other_app_on_cleanup=True)
        report = runner.run_probe(**self.kw, adb=fake)
        self.assertTrue(report['cleanup_pending'])
        self.assertFalse(report['passed'])
        self.assertFalse(any('#cleanupStagedInputAfterInterruptedProbe' in ' '.join(c)
                             for c in fake.calls))
        self.assertFalse(any('rm -f' in ' '.join(c) or 'force-stop' in ' '.join(c)
                             for c in fake.calls))

    def test_other_app_takes_foreground_during_private_cleanup(self):
        fake = self.fake(other_app_after_cleanup=True)
        report = runner.run_probe(**self.kw, adb=fake)
        self.assertTrue(report['cleanup_pending'])
        self.assertFalse(report['passed'])
        calls = [' '.join(c) for c in fake.calls]
        self.assertTrue(any('#cleanupStagedInputAfterInterruptedProbe' in c for c in calls))
        self.assertFalse(any('force-stop' in c or 'KEYCODE_HOME' in c for c in calls))

    def test_cleanup_instrumentation_failure_is_overall_failure_with_evidence(self):
        fake = self.fake(cleanup_result='FAILURES!!!\nTests run: 1, Failures: 1')
        report = runner.run_probe(**self.kw, adb=fake)
        self.assertFalse(report['passed'])
        self.assertTrue(report['cleanup_pending'])
        self.assertTrue(report['testcase_passed'])
        self.assertIn('FAILURES!!!', (self.kw['output'] / 'cleanup-instrumentation.txt').read_text())

    def test_cli_exits_nonzero_for_pending_cleanup(self):
        argv = ['probe', '--serial', 'SER123', '--mode', 'diagnostic', '--manifest', 'm',
                '--receipt', 'r', '--expected-source', 'b' * 40, '--output', 'o']
        with patch.object(runner, 'run_probe', return_value={'passed': False, 'cleanup_pending': True}), \
             patch.object(runner.sys, 'argv', argv):
            with self.assertRaises(SystemExit) as outcome:
                runner.main()
        self.assertEqual(outcome.exception.code, 1)

    def test_research_maps_private_source_to_approved_name_and_counts_result(self):
        assets = self.root / 'assets'
        assets.mkdir()
        pins = {}
        for name in ('model.tflite', 'holistic.task', 'labels.json', 'private-source.mp4'):
            path = assets / name
            path.write_bytes(name.encode())
            pins[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        data = {'schema_version': 1, 'model_sha256': pins['model.tflite'],
                'holistic_sha256': pins['holistic.task'], 'labels_sha256': pins['labels.json'],
                'clips': [{'clip_id': 'clip1', 'path': 'private-source.mp4',
                           'expected': 'HELLO2', 'sha256': pins['private-source.mp4']}]}
        self.manifest.write_text(json.dumps(data), encoding='utf-8')
        staged = dict(data, clips=[dict(data['clips'][0], path='clip1.mp4')])
        staged_bytes = json.dumps(staged, separators=(',', ':'), ensure_ascii=False).encode()
        fake = self.fake()
        fake.expected_stage = hashlib.sha256(staged_bytes).hexdigest()
        fake.stage_hashes = {'clip1.mp4': pins['private-source.mp4'], **{name: pins[name]
                             for name in ('model.tflite', 'holistic.task', 'labels.json')}}
        report = runner.run_probe(**dict(self.kw, mode='research', asset_dir=assets), adb=fake)
        calls = [' '.join(c) for c in fake.calls]
        self.assertIn('#recordedVideoToHolisticToLocalModel', next(c for c in calls if 'am instrument' in c))
        self.assertTrue(any('recognition-results/result.json' in c for c in calls))
        self.assertEqual(report['clip_count'], 1)
        bad = self.fake(result_clip_id='wrong')
        bad.expected_stage = fake.expected_stage
        bad.stage_hashes = fake.stage_hashes
        with self.assertRaises(runner.ProbeError):
            runner.run_probe(**dict(self.kw, mode='research', asset_dir=assets,
                                    output=self.root / 'fresh2', token='2' * 32), adb=bad)

    def test_research_rejects_traversal_before_adb(self):
        assets = self.root / 'assets'
        assets.mkdir()
        digest = 'a' * 64
        self.manifest.write_text(json.dumps({'schema_version': 1, 'model_sha256': digest,
            'holistic_sha256': digest, 'labels_sha256': digest,
            'clips': [{'clip_id': 'clip1', 'path': '../private.mp4',
                       'expected': 'HELLO2', 'sha256': digest}]}), encoding='utf-8')
        fake = self.fake()
        with self.assertRaises(runner.ProbeError):
            runner.run_probe(**dict(self.kw, mode='research', asset_dir=assets), adb=fake)
        self.assertEqual(fake.calls, [])

    def test_rejects_path_token_and_existing_output(self):
        for change in ({'token': '../bad'}, {'serial': 'bad space'}, {'output': self.root / 'already'}):
            if change.get('output'):
                change['output'].mkdir()
            fake = self.fake()
            with self.assertRaises(runner.ProbeError):
                runner.run_probe(**dict(self.kw, **change), adb=fake)
            self.assertEqual(fake.calls, [])


if __name__ == '__main__':
    unittest.main()
