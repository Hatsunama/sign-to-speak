import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('prepare-pocket-assets.py')
spec = importlib.util.spec_from_file_location('prepare_pocket_assets', SCRIPT)
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PocketAssetPreparation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = self.root / 'download'
        self.output = self.root / 'output'
        self.cargo = self.root / 'cargo'
        self.assets.mkdir()
        self.cargo.mkdir()
        self.expected = {}
        entries = []
        for name, rel, body in [
            ('model.safetensors', 'languages/english_18m_2026-10/model.safetensors', b'MODEL'),
            ('tokenizer.model', 'languages/english_18m_2026-10/tokenizer.model', b'TOKEN'),
            ('alba.safetensors', 'languages/english_18m_2026-10/embeddings/alba.safetensors', b'ALBA'),
        ]:
            path = self.assets / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            sha = hashlib.sha256(body).hexdigest()
            self.expected[name] = (len(body), sha)
            entries.append({'path': rel, 'size': len(body), 'sha256': sha})
        self.manifest = self.root / 'manifest.json'
        self.manifest.write_text(json.dumps({'schema': 1, 'model': {
            'repository': 'kyutai/pocket-tts-without-voice-cloning',
            'revision': 'a7056f505a4a8ca72d8b5fb1440071d6f1a35739',
            'language': 'english_18m_2026-10', 'voice': 'alba', 'assets': entries}}))
        packages, receipt = [], {}
        for number in range(133):
            name = f'fixture-{number}'
            folder = self.cargo / name
            folder.mkdir()
            (folder / 'Cargo.toml').write_text('[package]\n')
            notice = folder / 'LICENSE'
            notice.write_text(f'MIT license fixture {number}\n')
            pid = f'{name}@1.0.0|registry+https://example.test/index'
            packages.append({'id': pid, 'name': name, 'version': '1.0.0',
                             'source': 'registry+https://example.test/index',
                             'license': 'MIT', 'manifest_path': str(folder / 'Cargo.toml')})
            receipt[pid] = {'LICENSE': prepare.digest(notice)}
        self.metadata = self.root / 'metadata.json'
        self.metadata.write_text(json.dumps({'packages': packages}))
        self.receipt = self.root / 'receipt.json'
        self.receipt.write_text(json.dumps({'schema': 1, 'packages': receipt}))
        self.patch = patch.object(prepare, 'EXPECTED', self.expected)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        absent_supplement = patch.object(prepare, 'SUPPLEMENT_MANIFEST', self.root / 'absent-supplement.json')
        absent_supplement.start()
        self.addCleanup(absent_supplement.stop)

    def run_prepare(self):
        prepare.prepare(self.assets, self.metadata, self.output, manifest_path=self.manifest,
                        receipt_path=self.receipt, cargo_home=self.cargo,
                        allowed_output=self.output)

    def test_copies_only_three_real_assets_and_complete_notices(self):
        (self.assets / 'private.txt').write_text('do not bundle')
        self.run_prepare()
        self.assertEqual({p.name for p in (self.output / 'pocket-alba').iterdir()}, set(self.expected))
        for name in self.expected:
            self.assertEqual((self.output / 'pocket-alba' / name).read_bytes(),
                             next((self.assets / item['path']).read_bytes() for item in json.loads(self.manifest.read_text())['model']['assets'] if Path(item['path']).name == name))
        self.assertFalse((self.output / 'private.txt').exists())
        self.assertTrue((self.output / 'pocket-licenses/cargo/fixture-0-1.0.0/LICENSE').is_file())

    def test_missing_wrong_hash_and_oversize_rejected_before_output(self):
        model = self.assets / 'languages/english_18m_2026-10/model.safetensors'
        for body in (None, b'MODAL', b'MODEL!'):
            original = model.read_bytes()
            if body is None:
                model.unlink()
            else:
                model.write_bytes(body)
            with self.assertRaises(ValueError):
                self.run_prepare()
            self.assertFalse(self.output.exists())
            model.write_bytes(original)

    def test_linked_source_rejected(self):
        model = self.assets / 'languages/english_18m_2026-10/model.safetensors'
        outside = self.root / 'outside'
        outside.write_bytes(model.read_bytes())
        model.unlink()
        try:
            model.symlink_to(outside)
        except OSError:
            self.skipTest('symlinks unavailable')
        with self.assertRaises(ValueError):
            self.run_prepare()

    def test_partial_notice_receipt_does_not_replace_previous_output(self):
        self.run_prepare()
        first = (self.output / 'pocket-alba/model.safetensors').read_bytes()
        data = json.loads(self.receipt.read_text())
        data['packages'].pop(next(iter(data['packages'])))
        self.receipt.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.run_prepare()
        self.assertEqual((self.output / 'pocket-alba/model.safetensors').read_bytes(), first)

    def test_collect_receipt_needs_no_models_or_existing_receipt(self):
        self.manifest.unlink()
        self.receipt.unlink()
        candidate_dir = self.root / 'candidate'
        prepare.collect_receipt(self.metadata, candidate_dir, self.cargo)
        candidate = json.loads((candidate_dir / 'candidate-cargo-license-files.json').read_text())
        self.assertEqual(len(candidate['packages']), 133)
        self.assertEqual(candidate['schema'], 1)
        self.assertEqual(len(list((candidate_dir / 'candidate-notices').rglob('LICENSE'))), 133)

    def supplemental_fixture(self, name='dasp_frame', version='0.11.0'):
        metadata = json.loads(self.metadata.read_text())
        package = metadata['packages'][0]
        old_key = prepare.package_key(package)
        package.update(name=name, version=version,
                       source='registry+https://github.com/rust-lang/crates.io-index')
        new_key = prepare.package_key(package)
        (self.cargo / 'fixture-0/LICENSE').unlink()
        self.metadata.write_text(json.dumps(metadata))
        receipt = json.loads(self.receipt.read_text())
        receipt['packages'][new_key] = receipt['packages'].pop(old_key)
        self.receipt.write_text(json.dumps(receipt))
        supplement = self.root / 'supplemental'
        folder = supplement / f'{name}-{version}'
        folder.mkdir(parents=True)
        license_file = folder / 'LICENSE-MIT'
        license_file.write_text('Upstream MIT text for test\n')
        lock = self.root / 'Cargo.lock'
        checksum = 'a' * 64
        lock.write_text(f'[[package]]\nname = "{name}"\nversion = "{version}"\n'
                        'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
                        f'checksum = "{checksum}"\n')
        manifest = self.root / 'supplemental-manifest.json'
        manifest.write_text(json.dumps({'schema': 1, 'packages': {new_key: {
            'name': name, 'version': version, 'source': package['source'],
            'checksum': checksum, 'notices': {'upstream/LICENSE-MIT': {
                'path': f'{name}-{version}/LICENSE-MIT', 'sha256': prepare.digest(license_file)}}}}}))
        return manifest, supplement, lock, license_file, new_key

    def test_verified_supplement_joins_collector_and_bundle_inventory(self):
        manifest, supplement, lock, notice, key = self.supplemental_fixture()
        with patch.object(prepare, 'SUPPLEMENT_MANIFEST', manifest), \
             patch.object(prepare, 'SUPPLEMENT_ROOT', supplement), \
             patch.object(prepare, 'CARGO_LOCK', lock):
            candidate_dir = self.root / 'candidate'
            prepare.collect_receipt(self.metadata, candidate_dir, self.cargo)
            candidate = json.loads((candidate_dir / 'candidate-cargo-license-files.json').read_text())
            self.assertEqual(candidate['packages'][key],
                             {'upstream/LICENSE-MIT': prepare.digest(notice)})
            self.receipt.write_text(json.dumps(candidate))
            self.run_prepare()
            self.assertEqual((self.output / 'pocket-licenses/cargo/dasp_frame-0.11.0/'
                              'upstream/LICENSE-MIT').read_bytes(), notice.read_bytes())

    def test_reuse_directory_notice_and_xn_supplement_share_receipt_and_bundle(self):
        manifest, supplement, lock, mit, key = self.supplemental_fixture('xn', '0.2.12')
        crate = self.cargo / 'fixture-0'
        apache = crate / 'third_party/kleidiai/LICENSES/Apache-2.0.txt'
        apache.parent.mkdir(parents=True)
        apache.write_text('KleidiAI Apache fixture\n')
        licence = crate / 'third_party/example/LICENCES/BSD-3-Clause.txt'
        licence.parent.mkdir(parents=True)
        licence.write_text('Third-party BSD fixture\n')
        unrelated = crate / 'third_party/kleidiai/doc/overview.txt'
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text('Not a notice\n')
        with patch.object(prepare, 'SUPPLEMENT_MANIFEST', manifest), \
             patch.object(prepare, 'SUPPLEMENT_ROOT', supplement), \
             patch.object(prepare, 'CARGO_LOCK', lock):
            candidate_dir = self.root / 'candidate'
            prepare.collect_receipt(self.metadata, candidate_dir, self.cargo)
            candidate = json.loads((candidate_dir / 'candidate-cargo-license-files.json').read_text())
            self.assertEqual(candidate['packages'][key], {
                'third_party/kleidiai/LICENSES/Apache-2.0.txt': prepare.digest(apache),
                'third_party/example/LICENCES/BSD-3-Clause.txt': prepare.digest(licence),
                'upstream/LICENSE-MIT': prepare.digest(mit),
            })
            self.receipt.write_text(json.dumps(candidate))
            self.run_prepare()
            destination = self.output / 'pocket-licenses/cargo/xn-0.2.12'
            self.assertEqual((destination / 'third_party/kleidiai/LICENSES/Apache-2.0.txt').read_bytes(),
                             apache.read_bytes())
            self.assertEqual((destination / 'third_party/example/LICENCES/BSD-3-Clause.txt').read_bytes(),
                             licence.read_bytes())
            self.assertEqual((destination / 'upstream/LICENSE-MIT').read_bytes(), mit.read_bytes())
            self.assertFalse((destination / 'third_party/kleidiai/doc/overview.txt').exists())

    def test_r_efi_published_authors_is_inventory_notice(self):
        metadata = json.loads(self.metadata.read_text())
        package = metadata['packages'][0]
        package.update(name='r-efi', version='5.3.0',
                       source='registry+https://github.com/rust-lang/crates.io-index')
        (self.cargo / 'fixture-0/LICENSE').unlink()
        authors = self.cargo / 'fixture-0/AUTHORS'
        authors.write_text('Published full MIT grant and attribution fixture\n')
        found = prepare.inventory(metadata, self.cargo)
        self.assertEqual(found[prepare.package_key(package)], {'AUTHORS': authors})

    def test_supplement_refuses_changed_hash_identity_escape_and_link(self):
        manifest, supplement, lock, notice, key = self.supplemental_fixture()
        original = json.loads(manifest.read_text())
        with patch.object(prepare, 'SUPPLEMENT_MANIFEST', manifest), \
             patch.object(prepare, 'SUPPLEMENT_ROOT', supplement), \
             patch.object(prepare, 'CARGO_LOCK', lock):
            for mutation in ('hash', 'checksum', 'version', 'escape', 'link'):
                with self.subTest(mutation=mutation):
                    data = json.loads(json.dumps(original))
                    entry = data['packages'][key]
                    if mutation == 'hash':
                        entry['notices']['upstream/LICENSE-MIT']['sha256'] = '0' * 64
                    elif mutation == 'checksum':
                        entry['checksum'] = '0' * 64
                    elif mutation == 'version':
                        entry['version'] = '0.12.0'
                    elif mutation == 'escape':
                        entry['notices']['upstream/LICENSE-MIT']['path'] = '../private.txt'
                    else:
                        outside = self.root / 'private.txt'
                        outside.write_text('private')
                        notice.unlink()
                        try:
                            notice.symlink_to(outside)
                        except OSError:
                            self.skipTest('symlinks unavailable')
                    manifest.write_text(json.dumps(data))
                    with self.assertRaises(ValueError):
                        prepare.inventory(json.loads(self.metadata.read_text()), self.cargo)
                    if mutation == 'link':
                        notice.unlink()
                        notice.write_text('Upstream MIT text for test\n')

    def pinned_git_fixture(self):
        checkout = (self.cargo / 'git/checkouts/xn-ptts-1fae285e4acf79e2' /
                    prepare.PTTS_GIT_REV[:7])
        crate = checkout / 'ptts'
        crate.mkdir(parents=True)
        (crate / 'Cargo.toml').write_text('[package]\n')
        metadata = json.loads(self.metadata.read_text())
        metadata['packages'][0] = {
            'name': 'ptts', 'version': '1.0.0', 'source': prepare.PTTS_GIT_SOURCE,
            'manifest_path': str(crate / 'Cargo.toml')}
        return metadata, checkout, crate

    def test_pinned_git_parent_license_symlink_keeps_logical_label_and_resolved_hash(self):
        metadata, checkout, crate = self.pinned_git_fixture()
        official = checkout / 'LICENSE-MIT'
        official.write_text('official pinned source\n')
        link = crate / 'LICENSE-MIT'
        try:
            link.symlink_to('../LICENSE-MIT')
        except OSError:
            self.skipTest('symlinks unavailable')
        found = prepare.inventory(metadata, self.cargo)
        key = f'ptts@1.0.0|{prepare.PTTS_GIT_SOURCE}'
        self.assertEqual(set(found[key]), {'LICENSE-MIT'})
        self.assertEqual(prepare.digest(found[key]['LICENSE-MIT']), prepare.digest(official))
        output = self.root / 'candidate'
        prepare.collect_receipt(self.metadata_from(metadata), output, self.cargo)
        candidate = json.loads((output / 'candidate-cargo-license-files.json').read_text())
        self.assertEqual(candidate['packages'][key]['LICENSE-MIT'], prepare.digest(official))
        folder = hashlib.sha256(key.encode()).hexdigest()[:16]
        self.assertEqual((output / 'candidate-notices' / folder / 'LICENSE-MIT').read_text(),
                         official.read_text())

    def metadata_from(self, metadata):
        path = self.root / 'git-metadata.json'
        path.write_text(json.dumps(metadata))
        return path

    def test_pinned_git_license_symlink_escape_and_broken_rejected(self):
        metadata, checkout, crate = self.pinned_git_fixture()
        link = crate / 'LICENSE-MIT'
        other = self.cargo / 'git/checkouts/xn-ptts-0000000000000000' / checkout.name
        other.mkdir(parents=True)
        (other / 'LICENSE-MIT').write_text('other checkout\n')
        (self.cargo / 'LICENSE-MIT').write_text('CARGO_HOME root\n')
        (checkout / '.git').mkdir()
        (checkout / '.git/LICENSE-MIT').write_text('VCS internals\n')
        cases = [other / 'LICENSE-MIT', self.cargo / 'LICENSE-MIT',
                 checkout / '.git/LICENSE-MIT', checkout / 'absent/LICENSE-MIT']
        for target in cases:
            with self.subTest(target=target):
                try:
                    link.symlink_to(target)
                except OSError:
                    self.skipTest('symlinks unavailable')
                with self.assertRaises(ValueError):
                    prepare.inventory(metadata, self.cargo)
                link.unlink()

    def test_registry_license_symlink_stays_within_its_own_package(self):
        package = self.cargo / 'fixture-0'
        source = package / 'LICENSE'
        source.unlink()
        official = self.cargo / 'LICENSE'
        official.write_text('unrelated registry notice\n')
        try:
            source.symlink_to(official)
        except OSError:
            self.skipTest('symlinks unavailable')
        with self.assertRaises(ValueError):
            prepare.inventory(json.loads(self.metadata.read_text()), self.cargo)

    def test_workspace_inventory_ignores_generated_notices_and_keeps_nested_dependency_sources(self):
        own = self.root / 'own-runtime'
        own.mkdir()
        (own / 'Cargo.toml').write_text('[package]\n')
        (own / 'KYUTAI-MIT-LICENSE').write_text('source MIT\n')
        metadata = json.loads(self.metadata.read_text())
        metadata['packages'][0] = {'name': 'own-runtime', 'version': '1.0.0',
                                   'source': None, 'manifest_path': str(own / 'Cargo.toml')}
        nested = self.cargo / 'fixture-1' / 'third_party' / 'sentencepiece' / 'abseil'
        nested.mkdir(parents=True)
        (nested / 'LICENSE').write_text('source third party\n')
        with patch.object(prepare, 'NATIVE', self.root):
            # package_key recognizes this exact workspace source directory.
            runtime = self.root / 'vendor' / 'pocket-tts-runtime'
            runtime.parent.mkdir()
            own.rename(runtime)
            metadata['packages'][0]['manifest_path'] = str(runtime / 'Cargo.toml')
            before = prepare.inventory(metadata, self.cargo)
            for directory in ('target', 'generated', 'build'):
                generated = runtime / directory / 'release' / 'LICENSE'
                generated.parent.mkdir(parents=True)
                generated.write_text('generated phantom\n')
            after = prepare.inventory(metadata, self.cargo)
        self.assertEqual(before.keys(), after.keys())
        self.assertEqual({key: set(files) for key, files in before.items()},
                         {key: set(files) for key, files in after.items()})
        self.assertEqual(set(after['own-runtime@1.0.0|workspace']), {'KYUTAI-MIT-LICENSE'})
        self.assertIn('third_party/sentencepiece/abseil/LICENSE',
                      after['fixture-1@1.0.0|registry+https://example.test/index'])

    def test_output_outside_generated_root_keeps_existing_sentinel(self):
        outside = self.root / 'protected-keys'
        outside.mkdir()
        sentinel = outside / 'sentinel.key'
        sentinel.write_text('keep')
        with self.assertRaisesRegex(ValueError, 'output directory'):
            prepare.prepare(self.assets, self.metadata, outside, manifest_path=self.manifest,
                            receipt_path=self.receipt, cargo_home=self.cargo)
        self.assertEqual(sentinel.read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
