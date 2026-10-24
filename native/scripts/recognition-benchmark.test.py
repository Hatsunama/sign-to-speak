import hashlib
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

MODULE = pathlib.Path(__file__).with_name('recognition-benchmark.py')
spec = importlib.util.spec_from_file_location('recognition_benchmark', MODULE)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class BenchmarkTest(unittest.TestCase):
    """Synthetic bytes exercise the harness; these are not real sign videos."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.clips = []
        self.add('train-a', 'train-source', 'train-signer', 'train', 'isolated_sign', 'HELLO')
        self.add('test-a', 'test-source-a', 'test-signer-a', 'test', 'isolated_sign', 'HELLO')
        self.add('test-b', 'test-source-b', 'test-signer-b', 'test', 'isolated_sign', 'BYE')
        self.add('test-c', 'test-source-c', 'test-signer-c', 'test', 'fingerspelling', 'AAB!')
        self.add('test-d', 'test-source-d', 'test-signer-d', 'test', 'fingerspelling', '')
        self.manifest = self.root / 'manifest.json'
        self.save_manifest()

    def add(self, clip_id, source, signer, split, task, expected):
        data = ('SYNTHETIC ONLY ' + clip_id).encode()
        path = clip_id + '.bin'
        (self.root / path).write_bytes(data)
        self.clips.append(dict(clip_id=clip_id, source_id=source, signer_id=signer,
                               split=split, task=task, expected=expected,
                               path=path, sha256=sha(data)))

    def save_manifest(self):
        self.manifest.write_text(json.dumps(dict(schema_version=1, clips=self.clips)), encoding='utf-8')

    def predictions(self):
        digest = benchmark.validate_manifest(self.manifest)['dataset_sha256']
        records = [
            dict(clip_id='test-a', status='accepted', predicted='HELLO', elapsed_ms=1.2, model_sha256=sha(b'model')),
            dict(clip_id='test-b', status='abstain', elapsed_ms=1.3, model_sha256=sha(b'model')),
            dict(clip_id='test-c', status='accepted', predicted='AB?', elapsed_ms=2, model_sha256=sha(b'model')),
            dict(clip_id='test-d', status='accepted', predicted='!', elapsed_ms=2, model_sha256=sha(b'model')),
        ]
        (self.root / 'model.bin').write_bytes(b'model')
        return dict(schema_version=1, dataset_sha256=digest,
                    model=dict(path='model.bin', sha256=sha(b'model'), training_identity_scope='complete',
                               training_signer_ids=['train-signer'], training_source_ids=['train-source'],
                               training_dataset_sha256=sha(b'trainset'),
                               training_code_sha256=sha(b'traincode'), inference_code_sha256=sha(b'infercode')),
                    predictions=records)

    def save_predictions(self, value):
        path = self.root / 'predictions.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def test_validation_and_metrics(self):
        info = benchmark.validate_manifest(self.manifest)
        self.assertEqual(info['clip_count'], 5)
        result = benchmark.evaluate(self.manifest, self.save_predictions(self.predictions()))
        isolated = result['isolated_sign']
        self.assertEqual(isolated['count'], 2)
        self.assertEqual(isolated['top1_accuracy'], .5)
        self.assertEqual(isolated['coverage'], .5)
        self.assertEqual(isolated['accepted_precision'], 1)
        self.assertEqual(isolated['confusion']['BYE']['<abstain>'], 1)
        spelling = result['fingerspelling']
        self.assertEqual(spelling['count'], 2)
        self.assertEqual(spelling['character_errors'], 3)
        self.assertEqual(spelling['reference_characters'], 4)
        self.assertEqual(spelling['cer'], .75)
        self.assertEqual(spelling['exact_text_accuracy'], 0)

    def test_manifest_rejects_leakage_and_corruption(self):
        for field in ('signer_id', 'source_id'):
            with self.subTest(field=field):
                original = self.clips[1][field]
                self.clips[1][field] = self.clips[0][field]
                self.save_manifest()
                with self.assertRaises(benchmark.ValidationError):
                    benchmark.validate_manifest(self.manifest)
                self.clips[1][field] = original
        self.clips[1]['sha256'] = sha(b'bad')
        self.save_manifest()
        with self.assertRaises(benchmark.ValidationError):
            benchmark.validate_manifest(self.manifest)

    def test_manifest_rejects_escaping_path_and_duplicate_clip(self):
        self.clips[1]['path'] = '../escape.bin'
        self.save_manifest()
        with self.assertRaises(benchmark.ValidationError):
            benchmark.validate_manifest(self.manifest)
        self.clips[1]['path'] = self.clips[0]['path']
        self.clips[1]['clip_id'] = self.clips[0]['clip_id']
        self.save_manifest()
        with self.assertRaises(benchmark.ValidationError):
            benchmark.validate_manifest(self.manifest)

    def test_predictions_reject_missing_duplicate_extra_and_wrong_hashes(self):
        good = self.predictions()
        cases = []
        missing = json.loads(json.dumps(good)); missing['predictions'].pop(); cases.append(missing)
        duplicate = json.loads(json.dumps(good)); duplicate['predictions'].append(duplicate['predictions'][0]); cases.append(duplicate)
        extra = json.loads(json.dumps(good)); extra['predictions'].append(dict(clip_id='unknown', status='abstain', elapsed_ms=1, model_sha256=sha(b'model'))); cases.append(extra)
        wrong_dataset = json.loads(json.dumps(good)); wrong_dataset['dataset_sha256'] = sha(b'wrong'); cases.append(wrong_dataset)
        wrong_model = json.loads(json.dumps(good)); wrong_model['predictions'][0]['model_sha256'] = sha(b'wrong'); cases.append(wrong_model)
        for case in cases:
            with self.subTest(case=cases.index(case)), self.assertRaises(benchmark.ValidationError):
                benchmark.evaluate(self.manifest, self.save_predictions(case))

    def test_empty_reference_and_repeated_letters_are_literal(self):
        p = self.predictions()
        p['predictions'][2]['predicted'] = 'AB!'
        p['predictions'][3]['predicted'] = ''
        r = benchmark.evaluate(self.manifest, self.save_predictions(p))['fingerspelling']
        self.assertEqual(r['character_errors'], 1)
        self.assertEqual(r['exact_text_accuracy'], .5)


    def test_model_bytes_and_training_identity_overlap_rejected(self):
        p = self.predictions()
        p['model']['training_signer_ids'].append('test-signer-a')
        with self.assertRaises(benchmark.ValidationError):
            benchmark.evaluate(self.manifest, self.save_predictions(p))
        p['model']['training_signer_ids'].pop()
        (self.root / 'model.bin').write_bytes(b'corrupt')
        with self.assertRaises(benchmark.ValidationError):
            benchmark.evaluate(self.manifest, self.save_predictions(p))

    def test_unknown_training_identities_report_evidence_gap(self):
        p = self.predictions()
        p['model']['training_identity_scope'] = 'unknown'
        p['model']['training_signer_ids'] = []
        p['model']['training_source_ids'] = []
        result = benchmark.evaluate(self.manifest, self.save_predictions(p))
        self.assertFalse(result['heldout_identity_verified'])
        self.assertIn('cannot be verified', result['evidence_gap'])

    def test_requires_a_test_clip(self):
        self.clips = [c for c in self.clips if c['split'] == 'train']
        self.save_manifest()
        with self.assertRaisesRegex(benchmark.ValidationError, 'test'):
            benchmark.validate_manifest(self.manifest)

    def test_validation_split_identity_and_content_leakage(self):
        self.add('validation-a', 'train-source', 'validation-signer', 'validation', 'isolated_sign', 'HELLO')
        self.save_manifest()
        with self.assertRaisesRegex(benchmark.ValidationError, 'overlap'):
            benchmark.validate_manifest(self.manifest)
        self.clips[-1]['source_id'] = 'validation-source'
        self.clips[-1]['sha256'] = self.clips[0]['sha256']
        (self.root / self.clips[-1]['path']).write_bytes((self.root / self.clips[0]['path']).read_bytes())
        self.save_manifest()
        with self.assertRaisesRegex(benchmark.ValidationError, 'content'):
            benchmark.validate_manifest(self.manifest)

    def test_train_test_duplicate_content_rejected(self):
        self.clips[1]['sha256'] = self.clips[0]['sha256']
        (self.root / self.clips[1]['path']).write_bytes((self.root / self.clips[0]['path']).read_bytes())
        self.save_manifest()
        with self.assertRaisesRegex(benchmark.ValidationError, 'content'):
            benchmark.validate_manifest(self.manifest)

    def test_resolved_path_alias_rejected(self):
        alias = self.root / 'alias.bin'
        try:
            os.link(self.root / 'test-a.bin', alias)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'hard links unavailable: {exc}')
        self.clips[2]['path'] = 'alias.bin'
        self.clips[2]['sha256'] = self.clips[1]['sha256']
        self.save_manifest()
        with self.assertRaisesRegex(benchmark.ValidationError, 'path alias'):
            benchmark.validate_manifest(self.manifest)

    def test_known_training_overlap_rejected_even_with_unknown_scope(self):
        p = self.predictions()
        p['model']['training_identity_scope'] = 'unknown'
        p['model']['training_signer_ids'] = ['test-signer-a']
        with self.assertRaisesRegex(benchmark.ValidationError, 'overlap'):
            benchmark.evaluate(self.manifest, self.save_predictions(p))

    def test_self_declared_complete_scope_is_not_verified(self):
        result = benchmark.evaluate(self.manifest, self.save_predictions(self.predictions()))
        self.assertFalse(result['heldout_identity_verified'])
        self.assertIn('independently', result['evidence_gap'])

    def test_unknown_provenance_digests_are_explicit_gaps(self):
        p = self.predictions()
        p['model']['training_dataset_sha256'] = None
        p['model']['training_code_sha256'] = None
        p['model']['inference_code_sha256'] = None
        result = benchmark.evaluate(self.manifest, self.save_predictions(p))
        self.assertIsNone(result['training_provenance']['training_dataset_sha256'])
        self.assertIn('training_dataset_sha256', result['evidence_gap'])
        self.assertIn('inference_code_sha256', result['evidence_gap'])

if __name__ == '__main__':
    unittest.main()


