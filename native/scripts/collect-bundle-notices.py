#!/usr/bin/env python3
"""Collect bounded npm notice evidence from the actual QA bundle source map.

This inventory identifies source-map participants and preserves their installed
root notice bytes. It does not prove which notices the APK contains or grant
redistribution clearance; compare it with the separate APK asset audit.
"""

import hashlib
import json
import pathlib
import re
import sys
from urllib.parse import quote


MAX_MAP_BYTES = 64 * 1024 * 1024
MAX_SOURCES = 20000
MAX_SOURCE_LENGTH = 4096
MAX_LICENSE_BYTES = 1024 * 1024
MAX_LICENSE_FILES = 32
NOTICE_NAME = re.compile(r'^(?:license|licence|copying|notice)(?:[._-].*)?$', re.I)
ALIAS_MANIFEST = pathlib.Path(__file__).resolve().parent.parent / 'licenses' / 'framework' / 'npm-root-license-aliases.json'
REVIEWED_ALIAS_NAMES = frozenset({
    '@react-native/assets-registry', '@react-native/js-polyfills',
    '@react-native/normalize-colors', '@react-native/virtualized-lists',
})


def inside(path, root):
    return path == root or root in path.parents


def bounded_json(path, maximum):
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError('input missing or exceeds size limit')
    with path.open('rb') as stream:
        return json.load(stream)


def map_sources(mapping):
    sources = []

    def visit(item, depth):
        if depth > 4 or not isinstance(item, dict):
            raise ValueError('invalid or deeply nested source map')
        if 'sections' in item:
            sections = item['sections']
            if not isinstance(sections, list) or len(sections) > MAX_SOURCES:
                raise ValueError('invalid source-map sections')
            for section in sections:
                if not isinstance(section, dict) or 'map' not in section:
                    raise ValueError('external or invalid source-map section')
                visit(section['map'], depth + 1)
            return
        entries = item.get('sources')
        prefix = item.get('sourceRoot', '')
        if not isinstance(entries, list) or not isinstance(prefix, str):
            raise ValueError('source map has no sources')
        for entry in entries:
            if not isinstance(entry, str) or len(entry) > MAX_SOURCE_LENGTH:
                raise ValueError('invalid source-map path')
            # sourceRoot is a directory root, including when its spelling has
            # no trailing slash. Keep path components intact for candidate()'s
            # traversal check rather than normalizing away '..' here.
            source = (prefix.rstrip('/\\') + '/' + entry) if prefix else entry
            if len(source) > MAX_SOURCE_LENGTH:
                raise ValueError('source-map path exceeds limit')
            sources.append(source)
            if len(sources) > MAX_SOURCES:
                raise ValueError('source-map source count exceeds limit')

    visit(mapping, 0)
    return sources


def candidate(source, native, modules):
    normalized = source.replace('\\', '/')
    parts = normalized.split('/')
    if 'node_modules' not in parts:
        return None
    # Absolute paths in Metro maps must point to this checkout. Relative maps
    # may start with ../; use the first node_modules component as their anchor.
    if pathlib.PurePosixPath(normalized).is_absolute() or re.match(r'^[A-Za-z]:/', normalized):
        actual = pathlib.Path(source).resolve()
        if not inside(actual, modules):
            raise ValueError('npm source outside approved node_modules root')
        return actual
    offset = parts.index('node_modules')
    tail = parts[offset + 1:]
    if not tail or any(part in ('', '.', '..') for part in tail):
        raise ValueError('invalid npm source path')
    actual = modules.joinpath(*tail).resolve()
    if not inside(actual, modules):
        raise ValueError('npm source outside approved node_modules root')
    return actual


def package_for(source, modules):
    if not source.is_file():
        raise ValueError('unresolved npm source')
    relative = source.relative_to(modules)
    parts = relative.parts
    # The final node_modules segment owns this install. In-package manifests
    # (for example helpers/esm/package.json containing only `type`) are not
    # npm package boundaries.
    starts = [index + 1 for index, part in enumerate(parts) if part == 'node_modules']
    start = starts[-1] if starts else 0
    if start >= len(parts):
        raise ValueError('unresolved npm package metadata')
    width = 2 if parts[start].startswith('@') else 1
    if start + width >= len(parts):
        raise ValueError('unresolved npm package metadata')
    directory = modules.joinpath(*parts[:start + width])
    metadata = directory / 'package.json'
    if not inside(metadata.resolve(), directory):
        raise ValueError('package metadata outside approved root')
    package = bounded_json(metadata, 1024 * 1024)
    if not isinstance(package, dict):
        raise ValueError('invalid npm package metadata')
    name, version, license_field = (package.get(key) for key in ('name', 'version', 'license'))
    if not all(isinstance(value, str) and value.strip() for value in (name, version, license_field)):
        raise ValueError('missing npm package name, version, or license declaration')
    return directory, name, version, license_field


def reviewed_aliases():
    """Load only explicitly reviewed name/version/license grants from our source tree."""
    manifest = bounded_json(ALIAS_MANIFEST, 64 * 1024)
    if not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'aliases'} or manifest['schema_version'] != 1 or not isinstance(manifest['aliases'], list):
        raise ValueError('invalid reviewed alias manifest schema')
    framework = ALIAS_MANIFEST.parent.resolve(strict=True)
    aliases = {}
    required = {'name', 'version', 'license', 'source_file', 'sha256', 'source_url'}
    for alias in manifest['aliases']:
        if not isinstance(alias, dict) or set(alias) != required or not all(isinstance(alias[key], str) and alias[key] for key in required):
            raise ValueError('invalid reviewed alias entry')
        name, version, license_field = (alias[key] for key in ('name', 'version', 'license'))
        if name not in REVIEWED_ALIAS_NAMES or version != '0.86.3' or license_field != 'MIT':
            raise ValueError('unreviewed npm root license alias')
        source_file = alias['source_file']
        if source_file != 'react-native-LICENSE.txt' or alias['source_url'] != 'https://github.com/react/react-native/blob/v0.86.3/LICENSE':
            raise ValueError('reviewed alias source is not the pinned React Native grant')
        if not re.fullmatch(r'[0-9a-f]{64}', alias['sha256']):
            raise ValueError('invalid reviewed alias SHA-256')
        path = framework / source_file
        resolved = path.resolve(strict=True)
        if not inside(resolved, framework) or resolved != path or not resolved.is_file():
            raise ValueError('reviewed alias source outside framework root or symlinked')
        size = resolved.stat().st_size
        if size == 0 or size > MAX_LICENSE_BYTES:
            raise ValueError('reviewed alias source empty or exceeds limit')
        data = resolved.read_bytes()
        if len(data) != size or hashlib.sha256(data).hexdigest() != alias['sha256']:
            raise ValueError('reviewed alias source SHA-256 mismatch')
        key = (name, version, license_field)
        if key in aliases:
            raise ValueError('duplicate reviewed alias')
        aliases[key] = (alias, data)
    return aliases


def collect(native, map_path, output):
    native = native.resolve(strict=True)
    modules = (native / 'node_modules').resolve(strict=True)
    if not inside(modules, native):
        raise ValueError('node_modules outside native root')
    if output.exists():
        raise ValueError('output directory must be fresh')
    mapping = bounded_json(map_path, MAX_MAP_BYTES)
    matched = {}
    for index, source in enumerate(map_sources(mapping)):
        try:
            file = candidate(source, native, modules)
            if file is None:
                continue
            directory, name, version, license_field = package_for(file, modules)
            relative = file.relative_to(directory).as_posix()
            key = directory.as_posix()
            if key not in matched:
                matched[key] = {'directory': directory, 'name': name, 'version': version,
                                'license': license_field, 'sources': set()}
            matched[key]['sources'].add(relative)
        except ValueError as error:
            raise ValueError(f'source-map source #{index}: {error}') from error
    if not matched:
        raise ValueError('zero npm sources in generated QA source map')
    if len(matched) > MAX_SOURCES:
        raise ValueError('npm package count exceeds limit')

    # Validate all paths and reviewed aliases before creating evidence.
    aliases = reviewed_aliases()
    entries = []
    unresolved = 0
    for item in sorted(matched.values(), key=lambda p: (p['name'], p['version'], str(p['directory']))):
        directory = item['directory']
        files = sorted((p for p in directory.iterdir() if NOTICE_NAME.fullmatch(p.name)), key=lambda p: p.name)
        if len(files) > MAX_LICENSE_FILES:
            raise ValueError(f"{item['name']} {item['version']}: too many root notice files")
        package_id = quote(item['name'], safe='') + '-' + quote(item['version'], safe='')
        # A duplicated name/version at a different install path cannot safely
        # share an evidence directory, even if package metadata matches.
        if any(entry['evidence_directory'] == package_id for entry in entries):
            raise ValueError(f"{item['name']} {item['version']}: ambiguous duplicate installation")
        notices = []
        status = 'resolved'
        if not files:
            reviewed = aliases.get((item['name'], item['version'], item['license']))
            if reviewed is None:
                status = 'missing-root-notice'
                unresolved += 1
            else:
                alias, data = reviewed
                evidence_path = f'licenses/{package_id}/{alias["source_file"]}'
                notices.append({'source_file': alias['source_file'], 'evidence_path': evidence_path,
                                'size': len(data), 'sha256': alias['sha256'],
                                'selected_from': 'reviewed-alias',
                                'source_asset_uri': f'assets/framework/{alias["source_file"]}',
                                'source_url': alias['source_url'], '_bytes': data})
        for path in files:
            resolved = path.resolve()
            if not inside(resolved, directory) or not resolved.is_file():
                raise ValueError(f"{item['name']} {item['version']}: license text outside package root")
            size = resolved.stat().st_size
            if size == 0 or size > MAX_LICENSE_BYTES:
                raise ValueError(f"{item['name']} {item['version']}: license text empty or exceeds limit")
            data = resolved.read_bytes()
            if len(data) != size:
                raise ValueError('license text changed during collection')
            evidence_path = f'licenses/{package_id}/{path.name}'
            notices.append({'source_file': path.name, 'evidence_path': evidence_path,
                            'size': size, 'sha256': hashlib.sha256(data).hexdigest(), '_bytes': data})
        entries.append({'name': item['name'], 'version': item['version'], 'license': item['license'],
                        'install_path': directory.relative_to(modules).as_posix(),
                        'matched_sources': sorted(item['sources']),
                        'evidence_directory': package_id, 'notice_status': status, 'license_files': notices})

    output.mkdir(parents=True)
    for entry in entries:
        for notice in entry['license_files']:
            target = output / notice['evidence_path']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(notice.pop('_bytes'))
    report = {'status': 'INCOMPLETE' if unresolved else 'COMPLETE',
              'inventory_kind': 'actual-qa-source-map-npm-inventory',
              'scope': 'Installed package metadata and root notice bytes for npm sources in the generated QA bundle map; APK notice coverage and redistribution clearance require separate review.',
              'source_map_sha256': hashlib.sha256(map_path.read_bytes()).hexdigest(),
              'package_count': len(entries), 'unresolved_count': unresolved, 'packages': entries}
    (output / 'inventory.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return len(entries), unresolved


def main():
    if len(sys.argv) != 4:
        print('usage: collect-bundle-notices.py NATIVE_ROOT MAP_PATH FRESH_OUTPUT_DIR', file=sys.stderr)
        return 2
    try:
        count, unresolved = collect(*(pathlib.Path(value) for value in sys.argv[1:]))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f'Bundle notice collection failed: {error}', file=sys.stderr)
        return 1
    if unresolved:
        print(f'Bundle notice inventory INCOMPLETE: {unresolved} of {count} npm packages lack reviewed root license text', file=sys.stderr)
        return 1
    print(f'Collected root notice bytes for {count} npm packages from generated QA map; review required')
    return 0


if __name__ == '__main__':
    sys.exit(main())
