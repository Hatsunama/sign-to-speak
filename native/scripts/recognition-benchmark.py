#!/usr/bin/env python3
"""Offline clip-manifest validator and prediction evaluator. Standard library only.

Usage:
  python recognition-benchmark.py validate MANIFEST.json
  python recognition-benchmark.py evaluate MANIFEST.json PREDICTIONS.json

Version 1 manifest: {"schema_version":1,"clips":[...]}. Each clip has
clip_id, source_id, signer_id, split (train/validation/test), task
(isolated_sign/fingerspelling), expected (literal label/text, including an
empty fingerspelling string), path (relative to manifest), and sha256 of file
bytes. Dataset SHA256 is the canonical JSON SHA256 of the clips array
(sort_keys=True, separators=(',', ':'), ensure_ascii=False). All listed files
are checked; no clip is silently excluded. Validation needs no model.

Version 1 predictions: {"schema_version":1,"dataset_sha256":...,"model":
{"path":relative-to-predictions,"sha256":...,"training_dataset_sha256":...,
"training_code_sha256":...,"inference_code_sha256":...,
"training_identity_scope":"complete"|"unknown", "training_signer_ids":[],
"training_source_ids":[]},"predictions":[...]}. The identity lists must
be complete if scope is complete; unknown scope produces an evidence gap.
Provenance digests may be null when unknown. Declared identities cannot independently
prove that the model was trained only on those identities.
Each test clip must have exactly one record: {"clip_id":...,"model_sha256":...,
"elapsed_ms":nonnegative-number,"status":"accepted"|"abstain",
"predicted":literal-label-or-text} (omit predicted for abstain). No records
for non-test clips. The model file is byte-checked; training dataset/code
hashes are declared provenance and cannot be independently verified here.
A manifest must contain test clips; identity or byte overlap across splits is rejected.

Metrics use only the test split. An abstention counts as incorrect top-1 and
is a deletion for spelling CER. CER uses raw Unicode code points and no case,
spacing, punctuation, or repeated-letter normalization. Corpus CER denominator
is max(1, total reference characters), so empty references can contribute
insertions. This evaluates supplied clip predictions, not camera recognition,
on-device speed, or end-to-end use. Synthetic fixtures prove only the harness.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys


class ValidationError(ValueError):
    pass


def _fail(message):
    raise ValidationError(message)


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


def _hex(value, name):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        _fail(f'{name} must be a lowercase SHA256 hex digest')


def _string(value, name, allow_empty=False):
    if not isinstance(value, str) or (not allow_empty and not value):
        _fail(f'{name} must be a string' + ('' if allow_empty else ' and nonempty'))


def _keys(obj, required, name, optional=()):
    if not isinstance(obj, dict):
        _fail(f'{name} must be an object')
    absent = set(required) - obj.keys()
    unexpected = obj.keys() - set(required) - set(optional)
    if absent or unexpected:
        _fail(f'{name} keys invalid: missing={sorted(absent)}, unexpected={sorted(unexpected)}')


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f'Cannot read JSON {path}: {exc}')


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def _checked_file(base, relative, expected_hash, name):
    _string(relative, f'{name}.path')
    if '\\' in relative or Path(relative).is_absolute() or ':' in relative:
        _fail(f'{name}.path must be a local relative path')
    pieces = relative.split('/')
    if any(part in ('', '.', '..') for part in pieces):
        _fail(f'{name}.path has unsafe component')
    target = (base / relative).resolve()
    if not target.is_relative_to(base.resolve()) or not target.is_file():
        _fail(f'{name}.path escapes its directory or does not exist')
    _hex(expected_hash, f'{name}.sha256')
    if _sha256_file(target) != expected_hash:
        _fail(f'{name}.sha256 does not match file bytes')
    return target


def validate_manifest(manifest_path):
    manifest_path = Path(manifest_path)
    doc = _json(manifest_path)
    _keys(doc, ('schema_version', 'clips'), 'manifest')
    if type(doc['schema_version']) is not int or doc['schema_version'] != 1:
        _fail('Unsupported manifest schema_version')
    clips = doc['clips']
    if not isinstance(clips, list) or not clips:
        _fail('manifest.clips must be a nonempty array')
    ids, paths, file_ids = set(), set(), {}
    content_splits = {}
    by_split = {split: {'signers': set(), 'sources': set()} for split in ('train', 'validation', 'test')}
    for index, clip in enumerate(clips):
        name = f'clips[{index}]'
        _keys(clip, ('clip_id', 'source_id', 'signer_id', 'split', 'task', 'expected', 'path', 'sha256'), name)
        for field in ('clip_id', 'source_id', 'signer_id'):
            _string(clip[field], f'{name}.{field}')
        if clip['clip_id'] in ids:
            _fail(f'Duplicate clip_id: {clip["clip_id"]}')
        ids.add(clip['clip_id'])
        if clip['split'] not in by_split:
            _fail(f'{name}.split invalid')
        if clip['task'] not in ('isolated_sign', 'fingerspelling'):
            _fail(f'{name}.task invalid')
        _string(clip['expected'], f'{name}.expected', allow_empty=clip['task'] == 'fingerspelling')
        target = _checked_file(manifest_path.parent, clip['path'], clip['sha256'], name)
        file_stat = target.stat()
        file_id = (file_stat.st_dev, file_stat.st_ino)
        prior_path_split = file_ids.get(file_id)
        if prior_path_split is not None:
            _fail(f'Duplicate clip path alias across {prior_path_split} and {clip["split"]}: {clip["path"]}')
        file_ids[file_id] = clip['split']
        prior_content_split = content_splits.get(clip['sha256'])
        if prior_content_split is not None and prior_content_split != clip['split']:
            _fail(f'Clip content overlap between {prior_content_split} and {clip["split"]}: {clip["path"]}')
        content_splits[clip['sha256']] = clip['split']
        if clip['path'] in paths:
            _fail(f'Duplicate clip path: {clip["path"]}')
        paths.add(clip['path'])
        by_split[clip['split']]['signers'].add(clip['signer_id'])
        by_split[clip['split']]['sources'].add(clip['source_id'])
    if not any(clip['split'] == 'test' for clip in clips):
        _fail('manifest requires at least one test clip')
    for left, right in (('train', 'validation'), ('train', 'test'), ('validation', 'test')):
        for kind in ('signers', 'sources'):
            overlap = by_split[left][kind] & by_split[right][kind]
            if overlap:
                _fail(f'{kind} overlap between {left} and {right}: {sorted(overlap)}')
    return {'dataset_sha256': _digest(clips), 'clip_count': len(clips), 'clips': clips}


def _distance(reference, predicted):
    previous = list(range(len(predicted) + 1))
    for i, a in enumerate(reference, 1):
        current = [i]
        for j, b in enumerate(predicted, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def evaluate(manifest_path, predictions_path):
    manifest = validate_manifest(manifest_path)
    predictions_path = Path(predictions_path)
    doc = _json(predictions_path)
    _keys(doc, ('schema_version', 'dataset_sha256', 'model', 'predictions'), 'predictions file')
    if type(doc['schema_version']) is not int or doc['schema_version'] != 1:
        _fail('Unsupported predictions schema_version')
    _hex(doc['dataset_sha256'], 'dataset_sha256')
    if doc['dataset_sha256'] != manifest['dataset_sha256']:
        _fail('Predictions dataset_sha256 differs from validated manifest')
    model = doc['model']
    model_fields = ('path', 'sha256', 'training_dataset_sha256', 'training_code_sha256',
                    'inference_code_sha256', 'training_identity_scope',
                    'training_signer_ids', 'training_source_ids')
    _keys(model, model_fields, 'model')
    _checked_file(predictions_path.parent, model['path'], model['sha256'], 'model')
    for field in ('training_dataset_sha256', 'training_code_sha256', 'inference_code_sha256'):
        if model[field] is not None:
            _hex(model[field], f'model.{field}')
    if model['training_identity_scope'] not in ('complete', 'unknown'):
        _fail('model.training_identity_scope invalid')
    for field in ('training_signer_ids', 'training_source_ids'):
        values = model[field]
        if not isinstance(values, list) or len(values) != len(set(map(str, values))):
            _fail(f'model.{field} must be a unique array')
        for value in values:
            _string(value, f'model.{field} entry')
    train_clips = [c for c in manifest['clips'] if c['split'] == 'train']
    for field, clip_field in (('training_signer_ids', 'signer_id'), ('training_source_ids', 'source_id')):
        if model['training_identity_scope'] == 'complete' and not {c[clip_field] for c in train_clips}.issubset(model[field]):
            _fail(f'model.{field} omits manifest training identities')
        overlap = set(model[field]) & {c[clip_field] for c in manifest['clips'] if c['split'] == 'test'}
        if overlap:
            _fail(f'model.{field} overlaps test identities: {sorted(overlap)}')
    tests = {c['clip_id']: c for c in manifest['clips'] if c['split'] == 'test'}
    records = doc['predictions']
    if not isinstance(records, list):
        _fail('predictions must be an array')
    by_id = {}
    for index, record in enumerate(records):
        name = f'predictions[{index}]'
        _keys(record, ('clip_id', 'model_sha256', 'elapsed_ms', 'status'), name, ('predicted',))
        _string(record['clip_id'], f'{name}.clip_id')
        if record['clip_id'] in by_id:
            _fail(f'Duplicate prediction for {record["clip_id"]}')
        if record['clip_id'] not in tests:
            _fail(f'Extra or non-test prediction: {record["clip_id"]}')
        _hex(record['model_sha256'], f'{name}.model_sha256')
        if record['model_sha256'] != model['sha256']:
            _fail(f'{name}.model_sha256 differs from checked model')
        elapsed = record['elapsed_ms']
        if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
            _fail(f'{name}.elapsed_ms must be a finite nonnegative number')
        if record['status'] == 'accepted':
            if 'predicted' not in record:
                _fail(f'{name}.predicted missing')
            _string(record['predicted'], f'{name}.predicted', allow_empty=tests[record['clip_id']]['task'] == 'fingerspelling')
        elif record['status'] == 'abstain':
            if 'predicted' in record:
                _fail(f'{name}.abstain must omit predicted')
        else:
            _fail(f'{name}.status invalid')
        by_id[record['clip_id']] = record
    if set(by_id) != set(tests):
        _fail(f'Missing predictions: {sorted(set(tests) - set(by_id))}')
    gaps = []
    if model['training_identity_scope'] == 'complete':
        gaps.append('Training identity lists are self-declared; completeness cannot be independently verified.')
    else:
        gaps.append('Training signer/source identities are unknown; heldout identity cannot be verified.')
    unknown_digests = [field for field in ('training_dataset_sha256', 'training_code_sha256',
                                           'inference_code_sha256') if model[field] is None]
    if unknown_digests:
        gaps.append('Unknown provenance digests: ' + ', '.join(unknown_digests) + '.')
    result = {'dataset_sha256': manifest['dataset_sha256'], 'model_sha256': model['sha256'],
              'evaluator_sha256': _sha256_file(Path(__file__)),
              'training_provenance': {key: model[key] for key in model_fields if key != 'path'},
              'heldout_identity_verified': False,
              'training_identity_basis': 'self-declared complete lists; completeness is not independently audited' if model['training_identity_scope'] == 'complete' else 'unknown',
              'evidence_gap': ' '.join(gaps),
              'scope': 'offline supplied predictions on test clips; no camera or end-to-end proof'}
    isolated = [(c, by_id[c['clip_id']]) for c in tests.values() if c['task'] == 'isolated_sign']
    spelling = [(c, by_id[c['clip_id']]) for c in tests.values() if c['task'] == 'fingerspelling']
    if isolated:
        accepted = sum(p['status'] == 'accepted' for _, p in isolated)
        correct = sum(p['status'] == 'accepted' and p['predicted'] == c['expected'] for c, p in isolated)
        confusion = {}
        for c, p in isolated:
            truth = c['expected']
            predicted = p['predicted'] if p['status'] == 'accepted' else '<abstain>'
            row = confusion.setdefault(truth, {})
            row[predicted] = row.get(predicted, 0) + 1
        result['isolated_sign'] = {'count': len(isolated), 'accepted': accepted, 'correct': correct,
                                   'top1_accuracy': correct / len(isolated), 'coverage': accepted / len(isolated),
                                   'accepted_precision': correct / accepted if accepted else None,
                                   'confusion': confusion}
    if spelling:
        accepted = sum(p['status'] == 'accepted' for _, p in spelling)
        exact = sum(p['status'] == 'accepted' and p['predicted'] == c['expected'] for c, p in spelling)
        errors = sum(_distance(c['expected'], p['predicted'] if p['status'] == 'accepted' else '') for c, p in spelling)
        chars = sum(len(c['expected']) for c, _ in spelling)
        result['fingerspelling'] = {'count': len(spelling), 'accepted': accepted,
                                    'coverage': accepted / len(spelling), 'exact_text': exact,
                                    'exact_text_accuracy': exact / len(spelling),
                                    'character_errors': errors, 'reference_characters': chars,
                                    'cer': errors / max(1, chars)}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='mode', required=True)
    sub.add_parser('validate', help='Verify all manifest records and local clip bytes').add_argument('manifest')
    evaluator = sub.add_parser('evaluate', help='Verify supplied predictions and calculate test metrics')
    evaluator.add_argument('manifest')
    evaluator.add_argument('predictions')
    args = parser.parse_args(argv)
    try:
        output = validate_manifest(args.manifest) if args.mode == 'validate' else evaluate(args.manifest, args.predictions)
    except ValidationError as exc:
        parser.exit(2, f'Validation error: {exc}\n')
    if args.mode == 'validate':
        output = {key: value for key, value in output.items() if key != 'clips'}
    print(json.dumps(output, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())

