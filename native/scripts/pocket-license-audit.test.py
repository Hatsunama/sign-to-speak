import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name('pocket-license-audit.py')
spec = importlib.util.spec_from_file_location('pocket_license_audit', SCRIPT)
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


def package(name, license, version='1.0.0'):
    return {'name': name, 'version': version, 'license': license}


class AuditTests(unittest.TestCase):
    def test_historical_cargo_slash_declarations_keep_raw_and_selected_route(self):
        # Declarations observed in component-probe-40332632880, not invented aliases.
        declarations = [
            package('bitflags', 'MIT/Apache-2.0', '1.3.2'),
            package('enum-as-inner', 'MIT/Apache-2.0', '0.6.1'),
            package('same-file', 'Unlicense/MIT', '1.0.6'),
            package('version_check', 'MIT/Apache-2.0', '0.9.5'),
            package('walkdir', 'Unlicense/MIT', '2.5.0'),
            package('xn', 'MIT/Apache-2.0', '0.2.12'),
        ]
        report = audit_module.audit({'packages': declarations})
        self.assertEqual(report['blocked_packages'], [])
        for item in report['packages']:
            with self.subTest(name=item['name']):
                self.assertEqual(item['license'], next(p['license'] for p in declarations
                                                       if p['name'] == item['name']))
                self.assertEqual(item['normalized_expression'], item['license'].replace('/', ' OR '))
                self.assertTrue(item['legacy_slash_alias'])
                self.assertEqual(item['selected_route'], 'MIT')

    def test_mit_alternative_is_preferred_when_unlicense_text_is_not_present(self):
        for declaration in ('Unlicense OR MIT', 'MIT OR Unlicense', 'Unlicense/MIT'):
            with self.subTest(declaration=declaration):
                report = audit_module.audit({'packages': [package('dual', declaration)]})
                self.assertEqual(report['packages'][0]['selected_route'], 'MIT')
                self.assertEqual(report['packages'][0]['license'], declaration)

    def test_reported_alternatives_select_approved_route(self):
        report = audit_module.audit({'packages': [
            package('wasip2', 'Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT'),
            package('wit-bindgen', 'Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT'),
            package('ryu', 'Apache-2.0 OR BSL-1.0', '1.0.23'),
            package('r-efi', 'MIT OR Apache-2.0 OR LGPL-2.1-or-later', '5.3.0'),
        ]})
        self.assertEqual([p['name'] for p in report['packages']], ['r-efi', 'ryu', 'wasip2', 'wit-bindgen'])
        self.assertEqual([p['selected_route'] for p in report['packages']],
                         ['MIT', 'Apache-2.0', 'Apache-2.0', 'Apache-2.0'])
        self.assertEqual(report['blocked_packages'], [])
        self.assertIn('No redistribution clearance', report['scope'])

    def test_conjunction_precedence_and_parentheses(self):
        for expression, expected in [
            ('MIT OR LGPL-2.1-or-later AND Apache-2.0', 'MIT'),
            ('(MIT OR LGPL-2.1-or-later) AND Apache-2.0', 'MIT AND Apache-2.0'),
            ('(MIT AND Apache-2.0) OR BSL-1.0', 'MIT AND Apache-2.0'),
        ]:
            with self.subTest(expression=expression):
                self.assertEqual(audit_module.route(audit_module.parse(expression)), expected)
        self.assertIsNone(audit_module.route(audit_module.parse('MIT AND LGPL-2.1-or-later')))
        self.assertIsNone(audit_module.route(audit_module.parse('(MIT OR Apache-2.0) AND BSL-1.0')))

    def test_with_is_not_approved(self):
        self.assertIsNone(audit_module.route(audit_module.parse('Apache-2.0 WITH LLVM-exception')))
        self.assertEqual(audit_module.route(audit_module.parse('Apache-2.0 WITH LLVM-exception OR MIT')), 'MIT')

    def test_missing_unknown_and_malformed_are_pending(self):
        cases = [None, '', 'LGPL-2.1-or-later', 'MIT OR', 'MIT Apache-2.0',
                 'MIT | Apache-2.0', '(MIT OR Apache-2.0', 'MIT WITH',
                 'MIT AND LGPL-2.1-or-later', 'MIT/GPL-3.0',
                 'GPL-3.0/MIT', 'MIT/Apache-2.0/GPL-3.0',
                 'MIT / Apache-2.0', 'Unlicense/MIT/Apache-2.0']
        report = audit_module.audit({'packages': [package(str(i), value) for i, value in enumerate(cases)]})
        self.assertEqual(len(report['blocked_packages']), len(cases))
        self.assertTrue(all(not p['allowed_for_probe'] and p['selected_route'] is None
                            for p in report['packages']))

    def test_cli_writes_full_report_before_failing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            metadata = root / 'metadata.json'
            output = root / 'report.json'
            metadata.write_text(json.dumps({'packages': [package('ok', 'MIT'),
                                                         package('pending', 'BSL-1.0')]}))
            result = subprocess.run([sys.executable, str(SCRIPT), '--metadata', str(metadata),
                                     '--output', str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('pending 1.0.0', result.stderr)
            report = json.loads(output.read_text())
            self.assertEqual(len(report['packages']), 2)
            self.assertEqual(report['packages'][0]['selected_route'], 'MIT')


if __name__ == '__main__':
    unittest.main()
