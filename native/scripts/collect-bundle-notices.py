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

    # Validate every file before creating evidence, so failures leave no
    # partial report that could be mistaken for a complete inventory.
    entries = []
    for item in sorted(matched.values(), key=lambda p: (p['name'], p['version'], str(p['directory']))):
        directory = item['directory']
        files = sorted((p for p in directory.iterdir() if NOTICE_NAME.fullmatch(p.name)), key=lambda p: p.name)
        if not files:
            raise ValueError(f"{item['name']} {item['version']}: missing root license text")
        if len(files) > MAX_LICENSE_FILES:
            raise ValueError(f"{item['name']} {item['version']}: too many root notice files")
        package_id = quote(item['name'], safe='') + '-' + quote(item['version'], safe='')
        # A duplicated name/version at a different install path cannot safely
        # share an evidence directory, even if package metadata matches.
        if any(entry['evidence_directory'] == package_id for entry in entries):
            raise ValueError(f"{item['name']} {item['version']}: ambiguous duplicate installation")
        notices = []
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
                        'evidence_directory': package_id, 'license_files': notices})

    output.mkdir(parents=True)
    for entry in entries:
        for notice in entry['license_files']:
            target = output / notice['evidence_path']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(notice.pop('_bytes'))
    report = {'status': 'actual-qa-source-map-npm-inventory',
              'scope': 'Installed package metadata and root notice bytes for npm sources in the generated QA bundle map; APK notice coverage and redistribution clearance require separate review.',
              'source_map_sha256': hashlib.sha256(map_path.read_bytes()).hexdigest(),
              'package_count': len(entries), 'packages': entries}
    (output / 'inventory.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return len(entries)


def main():
    if len(sys.argv) != 4:
        print('usage: collect-bundle-notices.py NATIVE_ROOT MAP_PATH FRESH_OUTPUT_DIR', file=sys.stderr)
        return 2
    try:
        count = collect(*(pathlib.Path(value) for value in sys.argv[1:]))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f'Bundle notice collection failed: {error}', file=sys.stderr)
        return 1
    print(f'Collected root notice bytes for {count} npm packages from generated QA map; review required')
    return 0


if __name__ == '__main__':
    sys.exit(main())
