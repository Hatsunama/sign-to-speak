#!/usr/bin/env python3
"""Audit actual app APK detector model, native bytes, notices and privacy surface.

Run after the existing unsigned app/permission and full native ELF audit.
This does not claim execution on a phone or sign-recognition quality.
"""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET
import zipfile

spec = importlib.util.spec_from_file_location('holistic_assets', Path(__file__).with_name('prepare-holistic-assets.py'))
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)
ANDROID = '{http://schemas.android.com/apk/res/android}'


def dex_classes(blob):
    """Read actual class_def descriptors, not incidental strings/references."""
    if len(blob) < 112 or not re.fullmatch(rb'dex\n0[0-9]{2}\x00', blob[:8]):
        raise ValueError('Invalid DEX header')
    size, header, endian = struct.unpack_from('<III', blob, 32)
    if size != len(blob) or header != 112 or endian != 0x12345678:
        raise ValueError('Invalid DEX size or byte order')
    string_count, string_offset, type_count, type_offset = struct.unpack_from('<IIII', blob, 56)
    class_count, class_offset = struct.unpack_from('<II', blob, 96)
    if (string_count > len(blob) // 4 or string_offset + string_count * 4 > len(blob) or
            type_count > len(blob) // 4 or type_offset + type_count * 4 > len(blob) or
            class_count > len(blob) // 32 or class_offset + class_count * 32 > len(blob)):
        raise ValueError('DEX table outside file')
    result = set()
    for index in range(class_count):
        class_index, = struct.unpack_from('<I', blob, class_offset + index * 32)
        if class_index >= type_count:
            raise ValueError('DEX class type outside table')
        string_index, = struct.unpack_from('<I', blob, type_offset + class_index * 4)
        if string_index >= string_count:
            raise ValueError('DEX descriptor outside string table')
        offset, = struct.unpack_from('<I', blob, string_offset + string_index * 4)
        for _ in range(5):
            if offset >= len(blob):
                raise ValueError('DEX string outside file')
            byte = blob[offset]
            offset += 1
            if byte < 0x80:
                break
        else:
            raise ValueError('Invalid DEX string length')
        end = blob.find(b'\0', offset, min(offset + 4096, len(blob)))
        if end < 0:
            raise ValueError('DEX descriptor too long or unterminated')
        result.add(blob[offset:end])
    return result


def verify(manifest, apk, abi):
    if abi not in ('arm64-v8a', 'x86_64'):
        raise ValueError('Unreviewed app ABI')
    root = ET.parse(manifest).getroot()
    package = root.get('package')
    if package not in ('com.xmilo_at_your_side.sign_to_speak', 'com.xmilo_at_your_side.sign_to_speak.qa'):
        raise ValueError('Unexpected application package')
    expected = {'android.permission.CAMERA', package + '.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION'}
    permissions = [item.get(ANDROID + 'name') for item in root
                   if item.tag in ('uses-permission', 'uses-permission-sdk-23', 'uses-permission-sdk-m')]
    if len(permissions) != 2 or set(permissions) != expected:
        raise ValueError('Detector app must retain the exact no-network permission contract')
    application = root.find('application')
    if (application is None or application.get(ANDROID + 'allowAudioPlaybackCapture') != 'false' or
            application.get(ANDROID + 'allowBackup') != 'false' or
            application.get(ANDROID + 'usesCleartextTraffic') != 'false'):
        raise ValueError('Application privacy attributes changed')
    for node in application.iter():
        name = node.get(ANDROID + 'name', '')
        if node.tag in ('service', 'receiver', 'provider', 'activity', 'activity-alias') and (
                name.startswith(('com.google.mediapipe.', 'com.google.android.datatransport.', 'com.google.firebase.')) or
                'transport' in name.lower()):
            raise ValueError('Unexpected detector telemetry application component')
    raw, data = assets.receipt()
    assets.verify_offline_core()
    notices = assets.notice_sources(data)
    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip() is not None:
            raise ValueError('Duplicate or damaged APK ZIP entry')
        model = 'assets/' + assets.MODEL_ASSET
        if {n for n in names if n.startswith('assets/holistic/') and not n.endswith('/')} != {model}:
            raise ValueError('Unexpected APK detector model inventory')
        if archive.getinfo(model).file_size != assets.MODEL_SIZE:
            raise ValueError('APK Holistic model size mismatch')
        digest = hashlib.sha256()
        with archive.open(model) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        if digest.hexdigest() != assets.MODEL_SHA256:
            raise ValueError('APK Holistic model SHA-256 mismatch')
        wanted = {'assets/' + name for name in notices} | {'assets/holistic-licenses/MODEL-RECEIPT.json'}
        actual = {n for n in names if n.startswith('assets/holistic-licenses/') and not n.endswith('/')}
        if actual != wanted:
            raise ValueError('APK detector notice inventory mismatch')
        for asset, source in notices.items():
            if archive.read('assets/' + asset) != source.read_bytes():
                raise ValueError('APK detector license text changed')
        if archive.read('assets/holistic-licenses/MODEL-RECEIPT.json') != raw:
            raise ValueError('APK detector receipt changed')
        native = [n for n in names if n.endswith('/libmediapipe_tasks_jni.so')]
        if native != (['lib/arm64-v8a/libmediapipe_tasks_jni.so'] if abi == 'arm64-v8a' else []):
            raise ValueError('APK detector ABI inventory mismatch')
        if native and hashlib.sha256(archive.read(native[0])).hexdigest() != assets.JNI_SHA256:
            raise ValueError('APK detector JNI changed')
        dex_names = [n for n in names if re.fullmatch(r'classes(?:[2-9]|[1-9][0-9]+)?\.dex', n)]
        if any(n.lower().endswith('.dex') and n not in dex_names for n in names):
            raise ValueError('Unexpected DEX path outside the audited class inventory')
        dex = [archive.read(n) for n in dex_names]
        if not dex:
            raise ValueError('App DEX absent')
        for marker in assets.FORBIDDEN:
            if any(marker in blob for blob in dex):
                raise ValueError('Usage telemetry code present in APK DEX')
        defined = set().union(*(dex_classes(blob) for blob in dex))
        for name in (b'Lcom/google/mediapipe/tasks/vision/holisticlandmarker/HolisticLandmarker;',
                     b'Lcom/google/mediapipe/tasks/core/logging/TasksStatsDummyLogger;',
                     b'Lcom/google/mediapipe/tasks/core/logging/TasksStatsLoggerFactory;'):
            if name not in defined:
                raise ValueError('Required offline detector class absent from app DEX')
    print(f'Actual {abi} app detector model, offline SDK, notices and privacy surface verified.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('apk', type=Path)
    parser.add_argument('--abi', required=True, choices=('arm64-v8a', 'x86_64'))
    args = parser.parse_args()
    verify(args.manifest, args.apk, args.abi)
