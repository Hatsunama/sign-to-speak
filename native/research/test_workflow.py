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
    assert set(doc['jobs']) == {'research'}
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
