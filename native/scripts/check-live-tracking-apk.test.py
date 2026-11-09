import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zipfile

spec = importlib.util.spec_from_file_location('live_apk', Path(__file__).with_name('check-live-tracking-apk.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

CLASSES = [b'Lcom/google/mediapipe/tasks/vision/holisticlandmarker/HolisticLandmarker;',
           b'Lcom/google/mediapipe/tasks/core/logging/TasksStatsDummyLogger;',
           b'Lcom/google/mediapipe/tasks/core/logging/TasksStatsLoggerFactory;']


def dex(classes=CLASSES, extra=b''):
    count = len(classes)
    strings, types, definitions = 112, 112 + 4 * count, 112 + 8 * count
    start = 112 + 40 * count
    blob = bytearray(start)
    blob[:8] = b'dex\n035\x00'
    struct.pack_into('<II', blob, 36, 112, 0x12345678)
    struct.pack_into('<IIII', blob, 56, count, strings, count, types)
    struct.pack_into('<II', blob, 96, count, definitions)
    for index, name in enumerate(classes):
        struct.pack_into('<I', blob, strings + 4 * index, len(blob))
        struct.pack_into('<I', blob, types + 4 * index, index)
        struct.pack_into('<I', blob, definitions + 32 * index, index)
        blob += bytes([len(name)]) + name + b'\x00'
    blob += extra
    struct.pack_into('<I', blob, 32, len(blob))
    return bytes(blob)


class ApkTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = self.root / 'manifest.xml'
        self.xml = '''<manifest xmlns:android="http://schemas.android.com/apk/res/android"
          package="com.xmilo_at_your_side.sign_to_speak.qa">
          <uses-permission android:name="android.permission.CAMERA"/>
          <uses-permission android:name="com.xmilo_at_your_side.sign_to_speak.qa.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"/>
          <application android:allowAudioPlaybackCapture="false" android:allowBackup="false" android:usesCleartextTraffic="false"/>
        </manifest>'''
        self.manifest.write_text(self.xml)
        raw, data = m.assets.receipt()
        self.entries = {'assets/holistic-licenses/MODEL-RECEIPT.json': raw,
                        'assets/' + m.assets.MODEL_ASSET: b'synthetic model fixture',
                        'lib/arm64-v8a/libmediapipe_tasks_jni.so': b'synthetic JNI fixture',
                        'classes.dex': dex()}
        for asset, source in m.assets.notice_sources(data).items():
            self.entries['assets/' + asset] = source.read_bytes()
        patches = [('MODEL_SIZE', len(self.entries['assets/' + m.assets.MODEL_ASSET])),
                   ('MODEL_SHA256', hashlib.sha256(self.entries['assets/' + m.assets.MODEL_ASSET]).hexdigest()),
                   ('JNI_SHA256', hashlib.sha256(self.entries['lib/arm64-v8a/libmediapipe_tasks_jni.so']).hexdigest()),
                   ('receipt', lambda: (raw, data)), ('verify_offline_core', lambda: None)]
        for key, value in patches:
            patcher = mock.patch.object(m.assets, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.apk = self.root / 'app.apk'

    def verify(self, abi='arm64-v8a'):
        with zipfile.ZipFile(self.apk, 'w') as archive:
            for name, content in self.entries.items(): archive.writestr(name, content)
        m.verify(self.manifest, self.apk, abi)

    def test_exact_app_inventory_passes(self): self.verify()

    def test_x86_preserves_app_but_has_no_arm64_detector_native(self):
        self.entries.pop('lib/arm64-v8a/libmediapipe_tasks_jni.so')
        self.verify('x86_64')
        with self.assertRaisesRegex(ValueError, 'ABI inventory'): self.verify()

    def test_model_hash_size_missing_or_extra_rejected(self):
        name = 'assets/' + m.assets.MODEL_ASSET
        original = self.entries[name]
        for value in (b'', b'?' + original[1:]):
            self.entries[name] = value
            with self.assertRaisesRegex(ValueError, 'model .*mismatch'): self.verify()
        self.entries.pop(name)
        with self.assertRaisesRegex(ValueError, 'model inventory'): self.verify()
        self.entries[name] = original
        self.entries['assets/holistic/private.task'] = b'not admitted'
        with self.assertRaisesRegex(ValueError, 'model inventory'): self.verify()

    def test_changed_native_or_unexpected_abi_rejected(self):
        name = 'lib/arm64-v8a/libmediapipe_tasks_jni.so'
        self.entries[name] = b'changed'
        with self.assertRaisesRegex(ValueError, 'JNI changed'): self.verify()
        self.entries.pop(name)
        self.entries['lib/x86_64/libmediapipe_tasks_jni.so'] = b'wrong'
        with self.assertRaisesRegex(ValueError, 'ABI inventory'): self.verify('x86_64')

    def test_every_notice_and_receipt_is_required_byte_for_byte(self):
        for name in [n for n in self.entries if n.startswith('assets/holistic-licenses/')]:
            original = self.entries.pop(name)
            with self.assertRaisesRegex(ValueError, 'notice inventory'): self.verify()
            self.entries[name] = b'changed'
            with self.assertRaisesRegex(ValueError, 'license text changed|receipt changed'): self.verify()
            self.entries[name] = original

    def test_forbidden_telemetry_in_any_dex_is_rejected(self):
        for name in ('classes2.dex', 'classes10.dex', 'classes19.dex', 'classes100.dex'):
            for marker in m.assets.FORBIDDEN:
                self.entries[name] = dex(extra=marker)
                with self.subTest(name=name, marker=marker), self.assertRaisesRegex(ValueError, 'telemetry'):
                    self.verify()
            self.entries.pop(name)

    def test_nonstandard_dex_is_not_silently_ignored(self):
        for name in ('classes1.dex', 'classes01.dex', 'assets/unexpected.dex'):
            self.entries[name] = dex()
            with self.assertRaisesRegex(ValueError, 'Unexpected DEX path'): self.verify()
            self.entries.pop(name)

    def test_required_descriptor_must_be_a_defined_class(self):
        for name in CLASSES:
            self.entries['classes.dex'] = dex([c for c in CLASSES if c != name], extra=name)
            with self.assertRaisesRegex(ValueError, 'class absent'): self.verify()

    def test_invalid_dex_tables_fail_closed(self):
        for data in (b'not dex', dex()[:-1], dex()[:56] + b'\xff' * 8 + dex()[64:]):
            self.entries['classes.dex'] = data
            with self.assertRaisesRegex(ValueError, 'DEX'): self.verify()

    def test_no_network_manifest_and_component_contract(self):
        for fragment in ('<uses-permission android:name="android.permission.INTERNET"/>',
                         '<uses-permission-sdk-23 android:name="android.permission.ACCESS_NETWORK_STATE"/>'):
            self.manifest.write_text(self.xml.replace('</manifest>', fragment + '</manifest>'))
            with self.assertRaisesRegex(ValueError, 'no-network'): self.verify()
        for tag in ('service', 'receiver', 'provider', 'activity', 'activity-alias'):
            self.manifest.write_text(self.xml.replace('android:usesCleartextTraffic="false"/>',
                f'android:usesCleartextTraffic="false"><{tag} android:name="com.google.mediapipe.tasks.DataTransport"/></application>'))
            with self.assertRaisesRegex(ValueError, 'component'): self.verify()
        for attribute in ('allowAudioPlaybackCapture', 'allowBackup', 'usesCleartextTraffic'):
            self.manifest.write_text(self.xml.replace(f'{attribute}="false"', f'{attribute}="true"'))
            with self.assertRaisesRegex(ValueError, 'privacy attributes'): self.verify()


if __name__ == '__main__': unittest.main()
