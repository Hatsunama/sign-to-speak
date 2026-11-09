#!/usr/bin/env python3
"""Prepare the pinned public Holistic model and notices, never user media.

The Gradle asset producer calls this for every normal app variant. Downloads
occur only on the build host. --model-file supports a fully offline build.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

NATIVE = Path(__file__).resolve().parents[1]
VENDOR = NATIVE / 'vendor/mediapipe-offline'
RECEIPT = VENDOR / 'runtime-admission.json'
MODEL_URL = ('https://storage.googleapis.com/mediapipe-models/holistic_landmarker/'
             'holistic_landmarker/float16/1/holistic_landmarker.task')
MODEL_SIZE = 13683609
MODEL_SHA256 = 'e2dab61191e2dcd0a15f943d8e3ed1dce13c82dfa597b9dd39f562975a50c3f8'
MODEL_ASSET = 'holistic/holistic_landmarker.task'
NOTICE_PREFIX = 'holistic-licenses/'
NOTICE_NAMES = frozenset({'mediapipe-tasks-1.1.1-NOTICE', 'Apache-2.0-LICENSE',
                          'protobuf-v26.1-LICENSE', 'flogger-0.6-LICENSE', 'ATTRIBUTION.txt'})
CORE_VERSION = '1.1.1-offline-50505c8'
CORE_BASE = VENDOR / f'maven/com/google/mediapipe/tasks-core/{CORE_VERSION}/tasks-core-{CORE_VERSION}'
CORE_SHA256 = 'e0e9365d753b990f9b0d68e61274dfa5736d8812dbde5679095aec5ca28bd6b2'
JNI_SHA256 = 'd80dfdfc6e034b7595137d035a279eddf70b028587510e99ede4a7e634c342a0'
FORBIDDEN = (b'com/google/android/datatransport/', b'com.google.android.datatransport.',
             b'RemoteLoggingClient', b'ProtoLogger', b'MediaPipeLoggingProto', b'MediaPipeLoggingEnumsProto')


def verify_offline_core():
    source = no_links(Path(str(CORE_BASE) + '.aar'))
    if source.stat().st_size != 8325540 or file_digest(source) != CORE_SHA256:
        raise ValueError('Changed offline core AAR')
    with zipfile.ZipFile(source) as aar:
        names = aar.namelist()
        native = [n for n in names if n.startswith('jni/') and n.endswith('.so')]
        if native != ['jni/arm64-v8a/libmediapipe_tasks_jni.so']:
            raise ValueError('Offline core ABI inventory changed')
        if hashlib.sha256(aar.read(native[0])).hexdigest() != JNI_SHA256:
            raise ValueError('Offline core JNI differs from official publication')
        manifest = ET.fromstring(aar.read('AndroidManifest.xml'))
        if any(item.tag != 'uses-sdk' for item in manifest):
            raise ValueError('Offline core manifest has unexpected components or permissions')
        if aar.read('META-INF/NOTICE') != (VENDOR / 'licenses/sdk-licenses/mediapipe-tasks-1.1.1-NOTICE').read_bytes():
            raise ValueError('Offline core original NOTICE changed')
        with zipfile.ZipFile(io.BytesIO(aar.read('classes.jar'))) as classes:
            expected = {
                'TasksStatsLoggerFactory': '0ef21d5b01068d2dc7e9a694e1369942aea379f4274362c547763c512df6dfd4',
                'TasksStatsDummyLogger': '311dd5a5bcd8006909ac7af2737a0816da8ccdab7abcade6ad0ce775277c7da0',
            }
            for name, sha in expected.items():
                data = classes.read('com/google/mediapipe/tasks/core/logging/' + name + '.class')
                if hashlib.sha256(data).hexdigest() != sha:
                    raise ValueError('Changed source-built dummy logger or factory')
            for name in classes.namelist():
                if name.endswith('.class') and any(marker in classes.read(name) for marker in FORBIDDEN):
                    raise ValueError('Usage telemetry present in offline core classes')
    pom = ET.parse(no_links(Path(str(CORE_BASE) + '.pom'))).getroot()
    ns = {'m': 'http://maven.apache.org/POM/4.0.0'}
    dependencies = [(d.findtext('m:groupId', namespaces=ns), d.findtext('m:artifactId', namespaces=ns),
                     d.findtext('m:version', namespaces=ns), d.findtext('m:scope', namespaces=ns))
                    for d in pom.findall('m:dependencies/m:dependency', ns)]
    if sorted(dependencies) != sorted([
            ('com.google.flogger', 'flogger', '0.6', 'compile'),
            ('com.google.flogger', 'flogger-system-backend', '0.6', 'compile'),
            ('com.google.guava', 'guava', '27.0.1-android', 'compile'),
            ('com.google.protobuf', 'protobuf-javalite', '4.26.1', 'compile')]):
        raise ValueError('Offline core dependency inventory changed')


def no_links(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f'Linked path is not admitted: {path}')
    return path.resolve()


def file_digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def receipt():
    raw = no_links(RECEIPT).read_bytes()
    data = json.loads(raw)
    model = data['model']
    if (data['schema'] != 1 or model['url'] != MODEL_URL or
            model['asset'] != MODEL_ASSET or model['size'] != MODEL_SIZE or
            model['sha256'] != MODEL_SHA256 or model['license'] != 'Apache-2.0'):
        raise ValueError('Changed Holistic model admission pins')
    return raw, data


def verify_model(path, model):
    path = no_links(path)
    if (not path.is_file() or path.stat().st_size != MODEL_SIZE or
            file_digest(path) != MODEL_SHA256):
        raise ValueError('Holistic model size or SHA-256 mismatch')
    # The complete bundle is pinned, including models whose optional outputs
    # are disabled. Never silently drop or replace a member of the bundle.
    with zipfile.ZipFile(path) as bundle:
        expected = {row['name']: row for row in model['members']}
        names = bundle.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise ValueError('Unexpected Holistic bundle member set')
        for name, row in expected.items():
            if bundle.getinfo(name).file_size != row['size']:
                raise ValueError('Holistic bundle member size mismatch')
            if hashlib.sha256(bundle.read(name)).hexdigest() != row['sha256']:
                raise ValueError('Holistic bundle member SHA-256 mismatch')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError('Pinned model download must not redirect')


def download(destination):
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(MODEL_URL, timeout=60) as source, destination.open('xb') as output:
        if source.geturl() != MODEL_URL or source.status != 200:
            raise ValueError('Unexpected model download response')
        size = source.headers.get('Content-Length')
        if size is not None and int(size) != MODEL_SIZE:
            raise ValueError('Unexpected model download Content-Length')
        count = 0
        while block := source.read(min(1024 * 1024, MODEL_SIZE + 1 - count)):
            count += len(block)
            if count > MODEL_SIZE:
                raise ValueError('Model download exceeds pinned size')
            output.write(block)


def notice_sources(data):
    rows = data['notices']
    if (len(rows) != len(NOTICE_NAMES) or
            {row['asset'] for row in rows} != {NOTICE_PREFIX + n for n in NOTICE_NAMES}):
        raise ValueError('Changed Holistic notice inventory')
    result = {}
    for row in rows:
        source = no_links(VENDOR / row['source'])
        if (not source.is_relative_to(VENDOR.absolute()) or not source.is_file() or
                source.stat().st_size != row['size'] or file_digest(source) != row['sha256']):
            raise ValueError(f'Missing or changed Holistic notice: {row["asset"]}')
        result[row['asset']] = source
    return result


def prepare(output, model_file=None, allow_download=True):
    output = no_links(output)
    if output.exists() and not output.is_dir():
        raise ValueError('Holistic output must be a directory')
    raw, data = receipt()
    verify_offline_core()
    notices = notice_sources(data)
    output.parent.mkdir(parents=True, exist_ok=True)
    # A previously staged exact model can be reused without a network request.
    source = Path(model_file) if model_file is not None else output / MODEL_ASSET
    if source.exists() or source.is_symlink():
        verify_model(source, data['model'])
    elif model_file is not None or not allow_download:
        raise ValueError('Pinned Holistic model missing; provide --model-file for an offline build')
    else:
        source = None
    with tempfile.TemporaryDirectory(prefix='.holistic-stage-', dir=output.parent) as temporary:
        staged = Path(temporary)
        model = staged / MODEL_ASSET
        model.parent.mkdir()
        if source is None:
            download(model)
        else:
            shutil.copyfile(source, model)
        verify_model(model, data['model'])
        for asset, original in notices.items():
            target = staged / asset
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(original, target)
        (staged / NOTICE_PREFIX / 'MODEL-RECEIPT.json').write_bytes(raw)
        # Only replace this dedicated generated asset tree, after every check.
        if output.exists():
            for item in output.rglob('*'):
                no_links(item)
            shutil.rmtree(output)
        shutil.copytree(staged, output)
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-directory', type=Path, required=True)
    parser.add_argument('--model-file', type=Path)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    prepare(args.output_directory, args.model_file, not args.offline)
    print('Pinned Holistic model and exact offline runtime notices prepared.')
