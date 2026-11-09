"""Focused policy guards, not a complete GitHub Actions schema validator."""
from pathlib import Path
import re
import unittest

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/sign-roboflow-trackers.yml'


def validate(raw):
    doc = yaml.load(raw, Loader=yaml.BaseLoader)
    assert doc['permissions'] == {'contents': 'read'}
    assert set(doc['on']) == {'pull_request', 'workflow_dispatch'}
    assert set(doc['jobs']) == {'research', 'native_compile'}
    job = doc['jobs']['research']
    assert job['runs-on'] == 'ubuntu-24.04'
    assert int(job['timeout-minutes']) <= 10
    assert 'head.repo.full_name == github.repository' in job['if']
    assert "github.head_ref == 'codex/integrate-roboflow-trackers'" in job['if']
    # jobs.<job_id>.env cannot access the runner context, even though steps can.
    for value in job.get('env', {}).values():
        assert not re.search(r'\$\{\{[^}]*\brunner\.', value)
    steps = job['steps']
    assert steps[0]['uses'] == 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262'
    assert steps[0]['with']['persist-credentials'] == 'false'
    assert 'github.event.pull_request.head.sha' in steps[0]['with']['ref']
    commands = '\n'.join(step.get('run', '') for step in steps)
    assert '--require-hashes' in commands and '--only-binary=:all:' in commands
    assert 'git rev-parse HEAD' in commands and '$TESTED_SHA' in commands
    assert 'python -m unittest discover -s native/research' in commands
    assert sum('uses' in step for step in steps) == 1
    assert 'native_tracking_parity.py' in commands and 'sha256sum --check' in commands
    assert 'CameraRgbaPixelsHostTest.kt' in commands and 'java -jar' in commands
    native = doc['jobs']['native_compile']
    assert native['needs'] == 'research' and int(native['timeout-minutes']) <= 30
    assert native['runs-on'] == 'ubuntu-24.04'
    assert 'head.repo.full_name == github.repository' in native['if']
    native_commands = '\n'.join(step.get('run', '') for step in native['steps'])
    assert ':app:compileQaAndroidTestKotlin' in native_commands
    assert ':app:compileEmulatorQaAndroidTestJavaWithJavac' in native_commands
    assert ':app:stageQaReviewedLicenses' in native_commands
    assert 'android.builder.sdkDownload=false' in native_commands
    assert 'useDefaultDebugSigningConfigForProfileableBuildtypes=false' in native_commands
    assert ':app:testDebugUnitTest' in native_commands
    assert ':app:verifyQaOfflineLandmarkRuntime' in native_commands
    assert ':app:assembleQa :app:assembleQaAndroidTest' in native_commands
    assert 'check-live-tracking-apk.py' in native_commands
    assert 'ci-phone-qa-audit.sh' in native_commands
    assert 'check-pocket-apk-elf.py' in native_commands
    assert not re.search(r'assemble(?:Debug|Release)|bundleRelease|connectedAndroidTest|--licenses|keytool', native_commands)
    assert 'npm test' in native_commands and 'npm run typecheck' in native_commands
    assert 'scripts/*.test.py' in native_commands and 'scripts/*.test.mjs' in native_commands
    uploads = [step for step in native['steps'] if step.get('uses', '').startswith('actions/upload-artifact@')]
    assert len(uploads) == 1 and uploads[0]['with']['retention-days'] == '3'
    assert '.apk' not in uploads[0]['with']['path']
    for step in native['steps']:
        if 'uses' in step:
            assert re.fullmatch(r'[\w-]+/[\w-]+@[a-f0-9]{40}', step['uses'])


class WorkflowTests(unittest.TestCase):
    def test_current_policy(self):
        validate(WORKFLOW.read_text())

    def test_rejects_original_runner_context_failure(self):
        broken = WORKFLOW.read_text().replace("      CI: 'true'", "      CI: 'true'\n      MPLCONFIGDIR: ${{ runner.temp }}/matplotlib")
        with self.assertRaises(AssertionError):
            validate(broken)

    def test_rejects_extra_permissions_or_unpinned_install(self):
        for old, new in [('contents: read', 'contents: write'), ('--require-hashes', '')]:
            with self.subTest(change=old), self.assertRaises(AssertionError):
                validate(WORKFLOW.read_text().replace(old, new))


if __name__ == '__main__':
    unittest.main()
