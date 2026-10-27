import hashlib
import json
import pathlib
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
        return subprocess.run([sys.executable, str(SCRIPT), str(self.native), str(self.source_map), str(self.output)], capture_output=True, text=True)

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
