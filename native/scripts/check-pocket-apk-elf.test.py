import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile


spec = importlib.util.spec_from_file_location('pocket_apk_elf', Path(__file__).with_name('check-pocket-apk-elf.py'))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class PocketApkRetirementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        model_bytes = {name: name.encode() for name in audit.asset_module.POCKET_MODEL}
        model_pin = {name: (len(data), hashlib.sha256(data).hexdigest())
                     for name, data in model_bytes.items()}
        notice_root = self.root / 'prepared' / 'pocket-licenses'
        notice_root.mkdir(parents=True)
        (notice_root / 'NOTICE.txt').write_bytes(b'fixture notice')
        (notice_root / 'cargo-declaration-routes.json').write_text(json.dumps({
            'status': 'declarations-selected-for-component-probe',
            'packages': [{}] * 133, 'blocked_packages': []}))
        self.entries = {
            **{'assets/pocket-alba/' + name: data for name, data in model_bytes.items()},
            **{'assets/pocket-licenses/' + path.name: path.read_bytes() for path in notice_root.iterdir()},
            **{'lib/arm64-v8a/' + name: b'fixture' for name in (
                'libsign_pocket_tts_runtime.so', 'libprivate_pocket_tts.so', 'libc++_shared.so')},
        }
        self.patch_pin = patch.object(audit.asset_module, 'POCKET_MODEL', model_pin)
        self.patch_pin.start()
        self.addCleanup(self.patch_pin.stop)
        self.patch_root = patch.object(audit.asset_module, 'POCKET_ROOT', self.root / 'prepared')
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)

    def verify(self):
        apk = self.root / 'fixture.apk'
        with zipfile.ZipFile(apk, 'w') as archive:
            for name, data in self.entries.items():
                archive.writestr(name, data)
        # Asset pinning and the ZIP retirement check run against real archive entries.
        # ELF contract verification has separate file-driven parser tests.
        with patch.object(audit, 'check'), patch.object(audit.subprocess, 'check_output') as readelf:
            readelf.return_value = '  LOAD 0x000000 0x000000 0x000000 0x100 0x100 R E 0x4000\n'
            audit.main(apk, 'arm64-v8a', self.root / 'readelf', self.root)

    def test_tensorflow_lite_asset_and_library_are_allowed(self):
        self.entries['assets/models/hand_landmarks.tflite'] = b'TFL3'
        self.entries['lib/arm64-v8a/libtensorflowlite_jni.so'] = b'fixture'
        self.verify()

    def test_old_flite_library_is_rejected(self):
        self.entries['lib/arm64-v8a/libflite_usenglish.so'] = b'old engine'
        with self.assertRaisesRegex(ValueError, 'Retired Flite'):
            self.verify()

    def test_old_flite_path_is_rejected(self):
        self.entries['assets/misc/flite/voice.dat'] = b'old engine'
        with self.assertRaisesRegex(ValueError, 'Retired Flite'):
            self.verify()

    def test_old_private_tts_library_is_rejected(self):
        self.entries['lib/arm64-v8a/libprivate_tts.so'] = b'old engine'
        with self.assertRaisesRegex(ValueError, 'Retired Flite'):
            self.verify()


if __name__ == '__main__':
    unittest.main()
