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
import stat
import sys
import tempfile
import tomllib

NATIVE = Path(__file__).resolve().parents[1]
MODEL_MANIFEST = NATIVE / 'vendor/pocket-tts-audition/manifest.json'
LICENSE_RECEIPT = NATIVE / 'licenses/pocket-tts/cargo-license-files.json'
SUPPLEMENT_ROOT = NATIVE / 'licenses/pocket-tts/supplemental'
SUPPLEMENT_MANIFEST = NATIVE / 'licenses/pocket-tts/supplemental-manifest.json'
CARGO_LOCK = NATIVE / 'vendor/pocket-tts-runtime/Cargo.lock'
SUPPLEMENT_PACKAGES = frozenset({
    ('dasp_frame', '0.11.0'), ('dasp_sample', '0.11.0'),
    ('pulp-wasm-simd-flag', '0.1.1'),
    ('sentencepiece', '0.14.0'), ('valuable', '0.1.1'), ('xn', '0.2.12'),
})
MIT_SOURCE = NATIVE / 'vendor/pocket-tts-runtime/KYUTAI-MIT-LICENSE'
EXPECTED = {
    'model.safetensors': (111728528, '599329dcea2f2a8d17b575e1c6e11b78822453c9073040d55c26faadf4883188'),
    'tokenizer.model': (59339, 'd461765ae179566678c93091c5fa6f2984c31bbe990bf1aa62d92c64d91bc3f6'),
    'alba.safetensors': (4163992, '11554cbe9f3d3a3e10722932df9952bb44928e534f7af3200524659166b5627a'),
}
LICENSE_NAME = re.compile(r'^(?:LICENSE|LICENCE|COPYING|NOTICE|COPYRIGHT)(?:[._-].*)?$', re.I)
PTTS_GIT_REV = '7efa26973f81bba42cf2ace8da2c1eb9b605d54d'
PTTS_GIT_SOURCE = ('git+https://github.com/gabrieldemarmiesse/xn-ptts'
                   f'?rev={PTTS_GIT_REV}#{PTTS_GIT_REV}')


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


def pinned_ptts_checkout(package, root, cargo_home):
    """Return the one pinned Git checkout shared by its workspace crates."""
    if package.get('source') != PTTS_GIT_SOURCE:
        return None
    revision = root.parent
    checkout = revision.parent
    if (root.name != package['name'] or
            not re.fullmatch(r'xn-ptts-[0-9a-f]{16}', checkout.name) or
            not re.fullmatch(r'[0-9a-f]{7,40}', revision.name) or
            not PTTS_GIT_REV.startswith(revision.name) or
            checkout.parent.resolve() != (cargo_home / 'git/checkouts').resolve()):
        raise ValueError(f'Unexpected pinned Git checkout: {package["name"]}')
    current = root
    while current != cargo_home:
        if current.is_symlink():
            raise ValueError(f'Linked pinned Git checkout: {package["name"]}')
        if current == current.parent:
            raise ValueError(f'Pinned Git checkout outside CARGO_HOME: {package["name"]}')
        current = current.parent
    return revision


def regular_notice(path, root, checkout):
    if not path.is_symlink():
        return regular(path, root)
    if checkout is None or not LICENSE_NAME.fullmatch(path.name):
        raise ValueError(f'Linked notice outside pinned Git checkout: {path}')
    # Only a notice file itself may be linked. Its resolved official source
    # must be a regular notice in the same immutable checkout, not .git,
    # another Cargo checkout, or a file elsewhere in CARGO_HOME.
    try:
        target = path.resolve(strict=True)
        if (not target.is_relative_to(checkout) or
                not LICENSE_NAME.fullmatch(target.name) or
                {'.git', 'target', 'generated', 'build'} & set(target.relative_to(checkout).parts) or
                not stat.S_ISREG(target.lstat().st_mode)):
            raise ValueError(f'Escaped or non-notice linked source: {path}')
        current = path.parent
        while current != root:
            if current.is_symlink():
                raise ValueError(f'Linked notice parent: {path}')
            current = current.parent
        current = target.parent
        while current != checkout:
            if current.is_symlink() or current == current.parent:
                raise ValueError(f'Linked notice target parent: {path}')
            current = current.parent
    except (OSError, RuntimeError) as error:
        raise ValueError(f'Broken linked notice source: {path}') from error
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


def supplemental_notices(packages, ordinary):
    """Add reviewed upstream notices for six exact Cargo crates."""
    if not SUPPLEMENT_MANIFEST.exists():
        return
    if SUPPLEMENT_ROOT.is_symlink() or not SUPPLEMENT_ROOT.is_dir():
        raise ValueError('Supplemental notice root missing or linked')
    manifest = json.loads(regular(SUPPLEMENT_MANIFEST, SUPPLEMENT_ROOT.parent).read_text(encoding='utf-8'))
    if manifest.get('schema') != 1 or not isinstance(manifest.get('packages'), dict):
        raise ValueError('Invalid supplemental notice manifest')
    locked = tomllib.loads(regular(CARGO_LOCK, CARGO_LOCK.parent).read_text(encoding='utf-8'))
    lock_entries = {(p['name'], p['version'], p.get('source')): p
                    for p in locked.get('package', [])}
    by_key = {package_key(p): p for p in packages}
    for key, entry in manifest['packages'].items():
        if key not in by_key or not isinstance(entry, dict):
            raise ValueError(f'Unrecognized supplemental package: {key}')
        package = by_key[key]
        name, version, source = package['name'], package['version'], package.get('source')
        if ((name, version) not in SUPPLEMENT_PACKAGES or
                (entry.get('name'), entry.get('version'), entry.get('source')) != (name, version, source)):
            raise ValueError(f'Unexpected supplemental notice identity: {key}')
        locked_package = lock_entries.get((name, version, source))
        if not locked_package or entry.get('checksum') != locked_package.get('checksum'):
            raise ValueError(f'Supplemental notice does not match Cargo.lock: {key}')
        if source and source.startswith('registry+') and not re.fullmatch(r'[0-9a-f]{64}', entry.get('checksum', '')):
            raise ValueError(f'Invalid locked registry checksum: {key}')
        notices = entry.get('notices')
        if not isinstance(notices, dict) or not notices:
            raise ValueError(f'No supplemental notices: {key}')
        for label, spec in notices.items():
            if not isinstance(spec, dict) or set(spec) != {'path', 'sha256'}:
                raise ValueError(f'Invalid supplemental notice: {key}')
            path_string = spec['path']
            if (not isinstance(label, str) or not label.startswith('upstream/') or
                    not LICENSE_NAME.fullmatch(Path(label).name) or
                    not isinstance(path_string, str) or '\\' in path_string):
                raise ValueError(f'Unsafe supplemental notice label: {key}')
            rel_label, rel_path = Path(label), Path(path_string)
            if (rel_label.is_absolute() or len(rel_label.parts) != 2 or
                    label != f'upstream/{rel_label.name}' or
                    rel_path.is_absolute() or len(rel_path.parts) != 2 or
                    path_string != f'{name}-{version}/{rel_path.name}' or
                    not LICENSE_NAME.fullmatch(rel_path.name)):
                raise ValueError(f'Unsafe supplemental notice path: {key}')
            notice = regular(SUPPLEMENT_ROOT / rel_path, SUPPLEMENT_ROOT)
            if not re.fullmatch(r'[0-9a-f]{64}', spec['sha256']) or digest(notice) != spec['sha256']:
                raise ValueError(f'Supplemental notice SHA-256 changed: {key}/{label}')
            if label in ordinary[key]:
                raise ValueError(f'Colliding supplemental notice: {key}/{label}')
            ordinary[key][label] = notice


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
        checkout = pinned_ptts_checkout(package, root, cargo_home)
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
                in_license_directory = any(part.casefold() in {'licenses', 'licences'}
                                           for part in Path(directory).relative_to(root).parts)
                for name in files:
                    if LICENSE_NAME.fullmatch(name) or in_license_directory:
                        path = Path(directory) / name
                        found[path.relative_to(root).as_posix()] = path
            # r-efi 5.3.0 publishes its full MIT grant in AUTHORS rather than
            # a LICENSE-named file. Keep that actual crate source in inventory.
            if (package['name'], package['version'], package.get('source')) == (
                    'r-efi', '5.3.0', 'registry+https://github.com/rust-lang/crates.io-index'):
                authors = root / 'AUTHORS'
                if authors.is_file():
                    found['AUTHORS'] = authors
        if package.get('license_file'):
            rel = Path(package['license_file'])
            if rel.is_absolute() or '..' in rel.parts:
                raise ValueError(f'Unsafe Cargo license_file: {package["name"]}')
            if root.resolve() == own_crate and {'target', 'generated', 'build', '.git'} & set(rel.parts):
                raise ValueError(f'Generated Cargo license_file: {package["name"]}')
            found[rel.as_posix()] = root / rel
        for path in found.values():
            regular_notice(path, root, checkout)
        result[package_key(package)] = found
    supplemental_notices(packages, result)
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
