#!/usr/bin/env python3
"""Prepare only the pinned Alba model and reviewed notices for Android assets.

No download occurs here. A hosted Cargo metadata file and a separately reviewed
license-file receipt are required before any output is published.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

NATIVE = Path(__file__).resolve().parents[1]
MODEL_MANIFEST = NATIVE / 'vendor/pocket-tts-audition/manifest.json'
LICENSE_RECEIPT = NATIVE / 'licenses/pocket-tts/cargo-license-files.json'
MIT_SOURCE = NATIVE / 'vendor/pocket-tts-runtime/KYUTAI-MIT-LICENSE'
EXPECTED = {
    'model.safetensors': (111728528, '599329dcea2f2a8d17b575e1c6e11b78822453c9073040d55c26faadf4883188'),
    'tokenizer.model': (59339, 'd461765ae179566678c93091c5fa6f2984c31bbe990bf1aa62d92c64d91bc3f6'),
    'alba.safetensors': (4163992, '11554cbe9f3d3a3e10722932df9952bb44928e534f7af3200524659166b5627a'),
}
LICENSE_NAME = re.compile(r'^(?:LICENSE|LICENCE|COPYING|NOTICE|COPYRIGHT)(?:[._-].*)?$', re.I)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def regular(path, root):
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Missing, linked, or escaped source: {path}')
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError(f'Linked source: {path}')
        current = current.parent
    return path


def verify_model(asset_dir, manifest):
    if asset_dir.is_symlink() or not asset_dir.is_dir():
        raise ValueError('Verified model asset root missing or linked')
    model = manifest['model']
    if (manifest['schema'] != 1 or model['repository'] != 'kyutai/pocket-tts-without-voice-cloning'
            or model['revision'] != 'a7056f505a4a8ca72d8b5fb1440071d6f1a35739'
            or model['language'] != 'english_18m_2026-10' or model['voice'] != 'alba'):
        raise ValueError('Unreviewed model identity')
    entries = model['assets']
    if len(entries) != 3 or {Path(x['path']).name for x in entries} != EXPECTED.keys():
        raise ValueError('Expected exactly the three pinned Alba assets')
    sources = {}
    for entry in entries:
        name = Path(entry['path']).name
        if not entry['path'].startswith('languages/english_18m_2026-10/') or '..' in Path(entry['path']).parts:
            raise ValueError('Unapproved asset path')
        size, sha = EXPECTED[name]
        if (entry['size'], entry['sha256']) != (size, sha):
            raise ValueError(f'Changed pinned receipt: {name}')
        path = regular(asset_dir / entry['path'], asset_dir)
        if path.stat().st_size != size or digest(path) != sha:
            raise ValueError(f'Size or SHA-256 mismatch: {name}')
        sources[name] = path
    return sources


def package_key(package):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', package['name']) or not re.fullmatch(r'[A-Za-z0-9.+-]+', package['version']):
        raise ValueError('Invalid Cargo package name or version')
    source = package.get('source') or 'workspace'
    if source == 'workspace' and Path(package['manifest_path']).parent.resolve() != (NATIVE / 'vendor/pocket-tts-runtime').resolve():
        raise ValueError(f'Unapproved workspace package: {package["name"]}')
    return f'{package["name"]}@{package["version"]}|{source}'


def inventory(metadata, cargo_home):
    packages = metadata.get('packages', [])
    if len(packages) != 133 or len({package_key(p) for p in packages}) != 133:
        raise ValueError('Expected 133 uniquely identified locked Cargo packages')
    cargo_home = cargo_home.resolve()
    own_crate = (NATIVE / 'vendor/pocket-tts-runtime').resolve()
    result = {}
    for package in packages:
        root = Path(package['manifest_path']).parent
        if root.is_symlink() or not (root.resolve().is_relative_to(cargo_home) or root.resolve() == own_crate):
            raise ValueError(f'Cargo source outside hosted CARGO_HOME: {package["name"]}')
        found = {}
        if root.resolve() == own_crate:
            # The workspace crate can acquire target/, generated/, and build/
            # during CI. Its source notice is at the crate root, so inventory
            # only root-level source files rather than build output.
            for path in root.iterdir():
                if path.is_file() and (LICENSE_NAME.fullmatch(path.name) or
                                       path.name == 'KYUTAI-MIT-LICENSE'):
                    found[path.name] = path
        else:
            # Registry and Git packages can contain nested third-party notices
            # (for example SentencePiece/abseil). Prune only VCS/build trees.
            for directory, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = [name for name in dirs if name not in {'target', '.git'}]
                for name in files:
                    if LICENSE_NAME.fullmatch(name):
                        path = Path(directory) / name
                        found[path.relative_to(root).as_posix()] = path
        if package.get('license_file'):
            rel = Path(package['license_file'])
            if rel.is_absolute() or '..' in rel.parts:
                raise ValueError(f'Unsafe Cargo license_file: {package["name"]}')
            if root.resolve() == own_crate and {'target', 'generated', 'build', '.git'} & set(rel.parts):
                raise ValueError(f'Generated Cargo license_file: {package["name"]}')
            found[rel.as_posix()] = root / rel
        for path in found.values():
            regular(path, root)
        result[package_key(package)] = found
    return result


def collect_receipt(metadata_path, output_dir, cargo_home):
    if output_dir.is_symlink() or (output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir()))):
        raise ValueError('Candidate output directory must be empty and not linked')
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    found = inventory(metadata, Path(cargo_home))
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate = {'schema': 1, 'packages': {key: {rel: digest(path) for rel, path in files.items()}
                                          for key, files in sorted(found.items())}}
    (output_dir / 'candidate-cargo-license-files.json').write_text(json.dumps(candidate, indent=2) + '\n')
    for key, files in found.items():
        # Stable, path-safe destination does not encode private source paths.
        destination = hashlib.sha256(key.encode()).hexdigest()[:16]
        for rel, source in files.items():
            target = output_dir / 'candidate-notices' / destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    (output_dir / 'candidate-notices-index.json').write_text(json.dumps({
        key: hashlib.sha256(key.encode()).hexdigest()[:16] for key in sorted(found)}, indent=2) + '\n')


def license_sources(metadata, receipt, cargo_home):
    spec = importlib.util.spec_from_file_location('pocket_license_audit', NATIVE / 'scripts/pocket-license-audit.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.audit(metadata)
    if report['blocked_packages']:
        raise ValueError('Cargo declaration route pending: ' + '; '.join(report['blocked_packages']))
    packages = metadata.get('packages', [])
    found_by_package = inventory(metadata, cargo_home)
    if receipt.get('schema') != 1 or set(receipt.get('packages', {})) != set(found_by_package):
        raise ValueError('License receipt must enumerate every stable locked Cargo package')
    selected = {}
    for package in packages:
        key = package_key(package)
        expected_files = receipt['packages'][key]
        if not isinstance(expected_files, dict) or not expected_files:
            raise ValueError(f'Missing pinned notice inventory: {package["name"]}')
        found = found_by_package[key]
        if set(found) != set(expected_files):
            raise ValueError(f'License filename inventory changed: {package["name"]}')
        for rel, path in found.items():
            if not isinstance(expected_files[rel], str) or digest(path) != expected_files[rel]:
                raise ValueError(f'License SHA-256 changed: {package["name"]}/{rel}')
            key = (package['name'], package['version'], rel)
            if key in selected:
                raise ValueError(f'Colliding Cargo notice destination: {key}')
            selected[key] = path
    return report, selected


def prepare(asset_dir, metadata_path, output_dir, *, manifest_path=MODEL_MANIFEST,
            receipt_path=LICENSE_RECEIPT, cargo_home=None, allowed_output=None):
    allowed = Path(allowed_output) if allowed_output is not None else NATIVE / 'generated/pocket-assets'
    if output_dir.absolute() != allowed.absolute():
        raise ValueError('Pocket output directory must be the fixed native/generated/pocket-assets target')
    current = output_dir
    while True:
        if current.is_symlink():
            raise ValueError('Pocket output directory or parent is linked')
        if current == current.parent:
            break
        current = current.parent
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError('Pocket output directory is not a directory')
    if output_dir.resolve() == asset_dir.resolve() or output_dir.resolve().is_relative_to(asset_dir.resolve()) or asset_dir.resolve().is_relative_to(output_dir.resolve()):
        raise ValueError('Output and source asset directories must not overlap')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    sources = verify_model(asset_dir, manifest)
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    cargo_home = Path(cargo_home or os.environ.get('CARGO_HOME', Path.home() / '.cargo'))
    report, notices = license_sources(metadata, receipt, cargo_home)
    regular(MIT_SOURCE, NATIVE)
    if digest(MIT_SOURCE) != '23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3':
        raise ValueError('Kyutai MIT notice changed')
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pocket-assets-', dir=output_dir.parent) as temp:
        stage = Path(temp)
        model_out = stage / 'pocket-alba'
        model_out.mkdir()
        for name, source in sources.items():
            shutil.copyfile(source, model_out / name)
            size, sha = EXPECTED[name]
            if (model_out / name).stat().st_size != size or digest(model_out / name) != sha:
                raise ValueError(f'Copied model changed: {name}')
        notice_out = stage / 'pocket-licenses'
        notice_out.mkdir()
        shutil.copyfile(MIT_SOURCE, notice_out / 'KYUTAI-MIT-LICENSE')
        (notice_out / 'ATTRIBUTION.txt').write_text(
            'Pocket TTS English 18M October 2026 model and tokenizer by Kyutai, CC BY 4.0.\n'
            'Source: https://huggingface.co/kyutai/pocket-tts-without-voice-cloning/tree/a7056f505a4a8ca72d8b5fb1440071d6f1a35739/languages/english_18m_2026-10\n'
            'Alba voice embedding derived from Alba MacKenna voice recording, CC BY 4.0.\n'
            'Source: https://huggingface.co/kyutai/tts-voices/blob/main/README.md#alba-mackenna\n'
            'License: https://creativecommons.org/licenses/by/4.0/\n'
            'Changes: Original pinned model, tokenizer and Alba embedding are bundled without modification. '
            'Kyutai Pocket TTS 4.0.1 Rust source is adapted for a local Android C ABI, bounded requests and cancellation; see native/vendor/pocket-tts-runtime/MODIFICATIONS.md.\n',
            encoding='utf-8')
        (notice_out / 'MODEL-RECEIPT.json').write_text(json.dumps({
            'repository': manifest['model']['repository'],
            'revision': manifest['model']['revision'],
            'language': manifest['model']['language'],
            'voice': manifest['model']['voice'],
            'assets': manifest['model']['assets'],
        }, indent=2) + '\n', encoding='utf-8')
        for (name, version, rel), source in notices.items():
            target = notice_out / 'cargo' / f'{name}-{version}' / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        (notice_out / 'cargo-declaration-routes.json').write_text(json.dumps(report, indent=2) + '\n')
        if output_dir.exists():
            shutil.rmtree(output_dir)
        shutil.move(str(stage), output_dir)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset-dir', type=Path)
    parser.add_argument('--metadata', required=True, type=Path, help='Full hosted cargo metadata JSON for the locked Android runtime')
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--collect-license-receipt', action='store_true',
                        help='Create unadmitted notice inventory for human review, without model assets')
    args = parser.parse_args()
    try:
        if args.collect_license_receipt:
            collect_receipt(args.metadata, args.output_dir,
                            os.environ.get('CARGO_HOME', Path.home() / '.cargo'))
        else:
            if args.asset_dir is None:
                parser.error('--asset-dir is required for preparation')
            prepare(args.asset_dir, args.metadata, args.output_dir)
    except (OSError, KeyError, ValueError, TypeError) as error:
        print(f'Pocket asset preparation blocked: {error}', file=sys.stderr)
        return 1
    print('Collected unadmitted Cargo notice candidate' if args.collect_license_receipt else
          'Prepared three verified Alba assets and pinned Cargo notices')
    return 0


if __name__ == '__main__':
    sys.exit(main())
