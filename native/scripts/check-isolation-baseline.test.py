import importlib.util
import pathlib
import tempfile
import shutil
import subprocess
import os
import unittest
import xml.etree.ElementTree as ET
spec = importlib.util.spec_from_file_location('checker', pathlib.Path(__file__).with_name('check-isolation-baseline.py'))
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

class BaselineCheckerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.report = pathlib.Path(self.tmp.name) / 'result.xml'
        self.source = pathlib.Path(self.tmp.name) / 'NativeBackPrivacyTest.java'
        self.source.write_text('\n'.join(f'assertNull("{msg}", escaped);' for msg in checker.MESSAGES.values()))
        self.root = ET.Element('testsuite', failures='4', errors='0')
        for line, (name, message) in enumerate(checker.MESSAGES.items(), 1):
            for suffix in ('[28]', ''):
                case = ET.SubElement(self.root, 'testcase', name=name + suffix)
                fail = ET.SubElement(case, 'failure', type='java.lang.AssertionError', message=message + ' expected null, but was:<java.lang.UnsupportedOperationException: Injected CameraX cleanup failure>')
                fail.text = f'at x.NativeBackPrivacyTest.{name}(NativeBackPrivacyTest.java:{line})'
    def verify(self):
        ET.ElementTree(self.root).write(self.report)
        checker.verify(self.report, self.source)
    def test_precise_failures_accepted(self): self.verify()
    def test_setup_failure_rejected(self):
        self.root.find('testcase/failure').set('message', 'Precondition: actual start() must bind a Preview')
        with self.assertRaises(AssertionError): self.verify()
    def test_unapproved_prefix_rejected(self):
        failure = self.root.find('testcase/failure')
        failure.set('message', 'RuntimeError: ' + failure.get('message'))
        with self.assertRaises(AssertionError): self.verify()
    def test_wrong_line_rejected(self):
        self.root.find('testcase/failure').text = 'NativeBackPrivacyTest.java:999'
        with self.assertRaises(AssertionError): self.verify()
    def test_wrong_exception_rejected(self):
        self.root.find('testcase/failure').set('type', 'java.lang.IllegalStateException')
        with self.assertRaises(AssertionError): self.verify()
    def test_missing_api_rejected(self):
        self.root.remove(self.root.find('testcase'))
        with self.assertRaises(AssertionError): self.verify()
    def test_green_baseline_rejected(self):
        self.root.set('failures', '0')
        with self.assertRaises(AssertionError): self.verify()
class RealXmlShapeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.report = pathlib.Path(self.tmp.name) / 'actual.xml'
        self.source = pathlib.Path(self.tmp.name) / 'NativeBackPrivacyTest.java'
        lines = [''] * 232
        for line, message in zip((204, 232), checker.MESSAGES.values()):
            lines[line - 1] = f'assertNull("{message}", escaped);'
        self.source.write_text('\n'.join(lines))
        self.root = ET.parse(pathlib.Path(__file__).with_name('fixtures') / 'run4-isolation-baseline.xml').getroot()
    def verify(self):
        ET.ElementTree(self.root).write(self.report)
        checker.verify(self.report, self.source)
    def test_real_primary_assertions_match_gradle_xml(self):
        # Isolate the authentic primary records; the extra teardown records are
        # independently required to reject below. No primary content is altered.
        for case in self.root.findall('testcase'):
            for extra in case.findall('failure')[1:]: case.remove(extra)
        self.verify()
    def test_real_extra_teardown_failures_rejected(self):
        with self.assertRaises(AssertionError): self.verify()

class RestoreIntegrationTest(unittest.TestCase):
    def exercise(self, fake_exit):
        repository = pathlib.Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            paths = [
                'native/scripts/ci-isolation-baseline.sh',
                'native/scripts/check-isolation-baseline.py',
                'native/tests/fixtures/pre-isolation.patch',
                'native/android/app/src/main/java/com/xmilo_at_your_side/sign_to_speak/MainActivity.kt',
                'native/android/app/src/main/java/com/xmilo_at_your_side/sign_to_speak/PrivateCameraView.kt',
                'native/android/app/src/test/java/com/xmilo_at_your_side/sign_to_speak/NativeBackPrivacyTest.java',
            ]
            for path in paths:
                destination = root / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(repository / path, destination)
            gradlew = root / 'native/android/gradlew'
            gradlew.write_text('#!/bin/sh\nexit ' + str(fake_exit) + '\n')
            gradlew.chmod(0o755)
            def git(*args):
                return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)
            git('init', '-q')
            git('config', 'core.autocrlf', 'false')
            git('add', '.')
            git('-c', 'user.name=CI fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
            originals = {path: (root / path).read_bytes() for path in paths if path.endswith('.kt')}
            output = root / 'step-output'
            bash = (pathlib.Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/bash.exe'
                if os.name == 'nt' else 'bash')
            def shell_path(path):
                if os.name != 'nt': return str(path)
                converted = subprocess.run([str(bash), '-c', 'cygpath -u "$1"', '_', str(path)],
                    capture_output=True, text=True, check=True)
                return converted.stdout.strip()
            result = subprocess.run([str(bash), shell_path(root / 'native/scripts/ci-isolation-baseline.sh')], cwd=root,
                env={**os.environ, 'RUNNER_TEMP':shell_path(root / 'temp'),
                    'GITHUB_OUTPUT':shell_path(output)}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0, 'Missing reports or unexpected green must reject baseline')
            self.assertTrue(output.is_file(), f'Restore output missing; exit={result.returncode}; stderr={result.stderr}')
            self.assertIn('restored=true', output.read_text())
            for path, body in originals.items(): self.assertEqual((root / path).read_bytes(), body)
            git('diff', '--exit-code', 'HEAD', '--', *originals)
    def test_restores_after_gradle_failure_without_reports(self): self.exercise(1)
    def test_restores_after_unexpected_green(self): self.exercise(0)

if __name__ == '__main__': unittest.main()
