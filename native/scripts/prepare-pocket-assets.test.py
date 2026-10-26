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
