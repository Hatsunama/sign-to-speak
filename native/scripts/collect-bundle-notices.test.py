import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).with_name('collect-bundle-notices.py')


class BundleNoticeCollectorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.native = self.root / 'native'
        self.native.mkdir()
        (self.native / 'node_modules').mkdir()
        self.output = self.root / 'evidence'
        self.source_map = self.root / 'index.android.bundle.map'
        self.script = SCRIPT

    def package(self, relative, name, version, license_text=b'grant\r\n'):
        package = self.native / 'node_modules' / relative
        package.mkdir(parents=True)
        (package / 'package.json').write_text(json.dumps({'name': name, 'version': version, 'license': 'MIT'}))
        (package / 'LICENSE').write_bytes(license_text)
        (package / 'src').mkdir()
        (package / 'src' / 'index.js').write_text('export default 1;')
        return package

    def run_collector(self, sources, extra=None):
        self.source_map.write_text(json.dumps({'version': 3, 'sources': sources, 'sourcesContent': ['private'] * len(sources), 'mappings': 'secret', **(extra or {})}))
        return subprocess.run([sys.executable, str(self.script), str(self.native), str(self.source_map), str(self.output)], capture_output=True, text=True)

    def alias_fixture(self, sha=None, source_file='react-native-LICENSE.txt', version='0.86.3'):
        scripts = self.native / 'scripts'
        scripts.mkdir()
        self.script = scripts / SCRIPT.name
        shutil.copyfile(SCRIPT, self.script)
        framework = self.native / 'licenses' / 'framework'
        framework.mkdir(parents=True)
        grant = b'reviewed shared grant\r\n'
        (framework / 'react-native-LICENSE.txt').write_bytes(grant)
        alias = {'name': '@react-native/assets-registry', 'version': version, 'license': 'MIT',
                 'source_file': source_file, 'sha256': sha or hashlib.sha256(grant).hexdigest(),
                 'source_url': 'https://github.com/react/react-native/blob/v0.86.3/LICENSE'}
        manifest = framework / 'npm-root-license-aliases.json'
        manifest.write_text(json.dumps({'schema_version': 1, 'aliases': [alias]}))
        return manifest, grant

    def test_collects_exact_bytes_scoped_and_nested_packages_without_map_content(self):
        top = self.package('@acme/top', '@acme/top', '1.2.3', b'MIT\r\nexact\x00')
        nested = self.package('parent/node_modules/child', 'child', '2.0.0', b'child\n')
        result = self.run_collector(['../node_modules/@acme/top/src/index.js',
                                     str(nested / 'src' / 'index.js'),
                                     'src/App.tsx'])
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.output / 'inventory.json').read_text())
        self.assertEqual({p['name'] for p in report['packages']}, {'@acme/top', 'child'})
        self.assertNotIn('private', (self.output / 'inventory.json').read_text())
        self.assertNotIn('secret', (self.output / 'inventory.json').read_text())
        entry = next(p for p in report['packages'] if p['name'] == '@acme/top')
        self.assertEqual(entry['matched_sources'], ['src/index.js'])
        copied = self.output / entry['license_files'][0]['evidence_path']
        self.assertEqual(copied.read_bytes(), (top / 'LICENSE').read_bytes())
        self.assertEqual(entry['license_files'][0]['sha256'], hashlib.sha256(copied.read_bytes()).hexdigest())

    def test_missing_license_text_fails(self):
        package = self.package('pkg', 'pkg', '1.0.0')
        (package / 'LICENSE').unlink()
        result = self.run_collector(['node_modules/pkg/src/index.js'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('license text', result.stderr)
        report = json.loads((self.output / 'inventory.json').read_text())
        self.assertEqual(report['status'], 'INCOMPLETE')
        self.assertEqual(report['unresolved_count'], 1)
        self.assertEqual(report['packages'][0]['notice_status'], 'missing-root-notice')

    def test_missing_notice_retains_other_package_evidence_without_map_contents(self):
        good = self.package('good', 'good', '1.0.0', b'exact\r\n')
        missing = self.package('missing', 'missing', '1.0.0')
        (missing / 'LICENSE').unlink()
        result = self.run_collector(['node_modules/good/src/index.js', 'node_modules/missing/src/index.js'])
        self.assertEqual(result.returncode, 1)
        report = json.loads((self.output / 'inventory.json').read_text())
        self.assertEqual(report['package_count'], 2)
        self.assertEqual(report['unresolved_count'], 1)
        self.assertEqual((self.output / report['packages'][0]['license_files'][0]['evidence_path']).read_bytes(), (good / 'LICENSE').read_bytes())
        self.assertNotIn('private', (self.output / 'inventory.json').read_text())
        self.assertNotIn('secret', (self.output / 'inventory.json').read_text())

    def test_reviewed_alias_copies_exact_grant_with_provenance(self):
        _, grant = self.alias_fixture()
        package = self.package('@react-native/assets-registry', '@react-native/assets-registry', '0.86.3')
        (package / 'LICENSE').unlink()
        result = self.run_collector(['node_modules/@react-native/assets-registry/src/index.js'])
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = json.loads((self.output / 'inventory.json').read_text())['packages'][0]
        notice = entry['license_files'][0]
        self.assertEqual(notice['selected_from'], 'reviewed-alias')
        self.assertEqual(notice['source_asset_uri'], 'assets/framework/react-native-LICENSE.txt')
        self.assertEqual((self.output / notice['evidence_path']).read_bytes(), grant)

    def test_unknown_version_does_not_inherit_alias(self):
        self.alias_fixture()
        package = self.package('@react-native/assets-registry', '@react-native/assets-registry', '0.86.4')
        (package / 'LICENSE').unlink()
        result = self.run_collector(['node_modules/@react-native/assets-registry/src/index.js'])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads((self.output / 'inventory.json').read_text())['status'], 'INCOMPLETE')

    def test_wrong_license_declaration_does_not_inherit_alias(self):
        self.alias_fixture()
        package = self.package('@react-native/assets-registry', '@react-native/assets-registry', '0.86.3')
        (package / 'LICENSE').unlink()
        (package / 'package.json').write_text(json.dumps({'name': '@react-native/assets-registry', 'version': '0.86.3', 'license': 'BSD-3-Clause'}))
        result = self.run_collector(['node_modules/@react-native/assets-registry/src/index.js'])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads((self.output / 'inventory.json').read_text())['status'], 'INCOMPLETE')

    def test_bad_alias_hash_or_path_fails_without_inventory(self):
        self.alias_fixture()
        package = self.package('@react-native/assets-registry', '@react-native/assets-registry', '0.86.3')
        (package / 'LICENSE').unlink()
        manifest = self.native / 'licenses' / 'framework' / 'npm-root-license-aliases.json'
        original = json.loads(manifest.read_text())
        for invalid in ({'sha256': '0' * 64}, {'source_file': '../escape.txt'}):
            with self.subTest(invalid=invalid):
                self.output = self.root / ('evidence-' + str(len(list(self.root.glob('evidence-*')))))
                data = json.loads(json.dumps(original))
                data['aliases'][0].update(invalid)
                manifest.write_text(json.dumps(data))
                result = self.run_collector(['node_modules/@react-native/assets-registry/src/index.js'])
                self.assertEqual(result.returncode, 1)
                self.assertFalse((self.output / 'inventory.json').exists())

    def test_corrupt_alias_manifest_fails_closed(self):
        manifest, _ = self.alias_fixture()
        manifest.write_text('{broken')
        package = self.package('@react-native/assets-registry', '@react-native/assets-registry', '0.86.3')
        (package / 'LICENSE').unlink()
        result = self.run_collector(['node_modules/@react-native/assets-registry/src/index.js'])
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.output / 'inventory.json').exists())

    def test_unresolved_node_module_fails(self):
        result = self.run_collector(['node_modules/missing/index.js'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unresolved', result.stderr)

    def test_zero_npm_sources_fails(self):
        result = self.run_collector(['src/App.tsx'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('zero npm', result.stderr)

    def test_section_map_and_source_root(self):
        self.package('pkg', 'pkg', '1.0.0')
        result = self.run_collector([], {'sections': [{'offset': {'line': 0, 'column': 0},
            'map': {'version': 3, 'sourceRoot': '../', 'sources': ['node_modules/pkg/src/index.js']}}]})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.output / 'inventory.json').read_text())['package_count'], 1)

    def test_source_root_without_trailing_slash_resolves_folder(self):
        self.package('pkg', 'pkg', '1.0.0')
        result = self.run_collector(['src/index.js'], {'sourceRoot': '../node_modules/pkg'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.output / 'inventory.json').read_text())['packages'][0]['matched_sources'], ['src/index.js'])

    def test_source_root_cannot_hide_traversal(self):
        self.package('pkg', 'pkg', '1.0.0')
        result = self.run_collector(['../pkg/src/index.js'], {'sourceRoot': '../node_modules/pkg'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invalid npm source path', result.stderr)

    def test_nested_type_only_manifest_keeps_npm_package_boundary(self):
        package = self.package('@acme/runtime', '@acme/runtime', '1.2.3', b'root notice\r\n')
        nested = package / 'src' / 'esm'
        nested.mkdir()
        (nested / 'package.json').write_text('{"type":"module"}')
        (nested / 'index.js').write_text('export default 1;')
        result = self.run_collector(['node_modules/@acme/runtime/src/esm/index.js'])
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.output / 'inventory.json').read_text())
        self.assertEqual(report['package_count'], 1)
        self.assertEqual(report['packages'][0]['install_path'], '@acme/runtime')
        self.assertEqual((self.output / report['packages'][0]['license_files'][0]['evidence_path']).read_bytes(), b'root notice\r\n')

    def test_malformed_npm_root_cannot_use_ancestor_package(self):
        self.package('parent', 'parent', '1.0.0')
        child = self.native / 'node_modules' / 'parent' / 'node_modules' / 'child'
        child.mkdir(parents=True)
        (child / 'package.json').write_text('{"type":"module"}')
        (child / 'index.js').write_text('export default 1;')
        result = self.run_collector(['node_modules/parent/node_modules/child/index.js'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('missing npm package name, version, or license declaration', result.stderr)

    def test_source_traversal_fails(self):
        self.package('pkg', 'pkg', '1.0.0')
        result = self.run_collector(['node_modules/../outside.js'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invalid npm source path', result.stderr)

    def test_absolute_source_outside_approved_root_fails(self):
        self.package('pkg', 'pkg', '1.0.0')
        outside = self.root / 'other' / 'node_modules' / 'pkg' / 'index.js'
        outside.parent.mkdir(parents=True)
        outside.write_text('private')
        result = self.run_collector([str(outside)])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('outside approved node_modules root', result.stderr)

    def test_source_symlink_escape_fails(self):
        package = self.package('pkg', 'pkg', '1.0.0')
        outside = self.root / 'outside.js'
        outside.write_text('private')
        (package / 'src' / 'index.js').unlink()
        try:
            (package / 'src' / 'index.js').symlink_to(outside)
        except OSError as error:
            self.skipTest(f'symlinks unavailable: {error}')
        result = self.run_collector(['node_modules/pkg/src/index.js'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('outside', result.stderr)

    def test_license_symlink_escape_fails(self):
        package = self.package('pkg', 'pkg', '1.0.0')
        outside = self.root / 'outside-license'
        outside.write_text('private')
        (package / 'LICENSE').unlink()
        try:
            (package / 'LICENSE').symlink_to(outside)
        except OSError as error:
            self.skipTest(f'symlinks unavailable: {error}')
        result = self.run_collector(['node_modules/pkg/src/index.js'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('outside', result.stderr)


if __name__ == '__main__':
    unittest.main()
