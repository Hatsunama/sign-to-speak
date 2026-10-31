import hashlib
import importlib.util
import json
from pathlib import Path
import struct
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
            **{'assets/sign-to-speak/' + name:
               (audit.asset_module.LICENSE_ROOT / 'sign-to-speak' / name).read_bytes()
               for name in audit.asset_module.REQUIRED_APPLICATION_ASSETS},
            'assets/react-native-template-MIT.txt':
                (audit.asset_module.LICENSE_ROOT / 'react-native-template-MIT.txt').read_bytes(),
            **{'assets/framework/' + name:
               (audit.asset_module.LICENSE_ROOT / 'framework' / name).read_bytes()
               for name in audit.asset_module.REQUIRED_FRAMEWORK_ASSETS},
            **{'lib/arm64-v8a/' + name: b'fixture' for name in (
                'libsign_pocket_tts_runtime.so', 'libprivate_pocket_tts.so', 'libc++_shared.so')},
        }
        receipt = json.loads((audit.asset_module.LICENSE_ROOT / 'bundled-js' / 'reviewed-manifest.json').read_text())
        self.entries['assets/bundled-js/reviewed-manifest.json'] = (audit.asset_module.LICENSE_ROOT / 'bundled-js' / 'reviewed-manifest.json').read_bytes()
        for asset in {row['asset'] for row in receipt['packages'] if row['asset'].startswith('bundled-js/')}:
            self.entries['assets/' + asset] = (audit.asset_module.LICENSE_ROOT / asset).read_bytes()
        self.patch_pin = patch.object(audit.asset_module, 'POCKET_MODEL', model_pin)
        self.patch_pin.start()
        self.addCleanup(self.patch_pin.stop)
        self.patch_root = patch.object(audit.asset_module, 'POCKET_ROOT', self.root / 'prepared')
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)

    def test_missing_framework_notice_is_rejected(self):
        del self.entries['assets/framework/ndk-libcxxabi-NOTICE.txt']
        with self.assertRaisesRegex(AssertionError, 'framework license asset'):
            self.verify()

    def test_changed_framework_notice_is_rejected(self):
        self.entries['assets/framework/react-native-safe-area-context-LICENSE.txt'] = b'changed'
        with self.assertRaisesRegex(AssertionError, 'framework license asset'):
            self.verify()

    def verify(self):
        apk = self.stored_apk({name: 0 for name in self.entries if name.endswith('.so')})
        # Asset pinning and the ZIP retirement check run against real archive entries.
        # ELF contract verification has separate file-driven parser tests.
        with patch.object(audit, 'check'), patch.object(audit.subprocess, 'check_output') as readelf:
            readelf.side_effect = lambda command, **kwargs: self.mock_readelf(command,
                '  LOAD 0x000000 0x000000 0x000000 0x100 0x100 R E 0x4000\n')
            audit.main(apk, 'arm64-v8a', self.root / 'readelf', self.root)

    @staticmethod
    def mock_readelf(command, segments):
        if command[1] == '-hW':
            return 'Type: DYN (Shared object file)\nMachine: AArch64\n'
        return segments

    def stored_apk(self, native_residues):
        """Write real stored entries whose local payload offsets have chosen residues."""
        apk = self.root / 'aligned-fixture.apk'
        with zipfile.ZipFile(apk, 'w') as archive:
            for name, data in self.entries.items():
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_STORED
                if name in native_residues:
                    # Local header is 30 bytes plus filename and extra. Use a
                    # well-formed private ZIP extra field to place the payload.
                    start = archive.fp.tell() + 30 + len(name.encode())
                    padding = (native_residues[name] - start) % 16384
                    if padding < 4:
                        padding += 16384
                    info.extra = struct.pack('<HH', 0xCAFE, padding - 4) + bytes(padding - 4)
                archive.writestr(info, data)
        with zipfile.ZipFile(apk) as archive, apk.open('rb') as raw:
            for name, expected in native_residues.items():
                entry = archive.getinfo(name)
                raw.seek(entry.header_offset)
                header = raw.read(30)
                self.assertEqual(header[:4], b'PK\x03\x04')
                filename_length, extra_length = struct.unpack_from('<HH', header, 26)
                offset = entry.header_offset + 30 + filename_length + extra_length
                self.assertEqual(offset % 16384, expected, name)
                self.assertEqual(entry.compress_type, zipfile.ZIP_STORED)
        return apk

    def audit_with_readelf(self, apk, readelf_output):
        with patch.object(audit, 'check'), patch.object(audit.subprocess, 'check_output',
                                                     side_effect=lambda command, **kwargs:
                                                     self.mock_readelf(command,
                                                     readelf_output(command, **kwargs))) as readelf:
            audit.main(apk, 'arm64-v8a', self.root / 'readelf', self.root)
        return readelf

    def test_tensorflow_lite_asset_and_library_are_allowed(self):
        self.entries['assets/models/hand_landmarks.tflite'] = b'TFL3'
        self.entries['lib/arm64-v8a/libtensorflowlite_jni.so'] = b'fixture'
        self.verify()

    def test_all_stored_native_libraries_at_16k_offsets_are_accepted(self):
        self.entries['assets/models/hand_landmarks.tflite'] = b'TFL3'
        self.entries['lib/arm64-v8a/libtensorflowlite_jni.so'] = b'fixture'
        native = {name: 0 for name in self.entries if name.endswith('.so')}
        apk = self.stored_apk(native)
        aligned = '  LOAD 0x000000 0x000000 0x000000 0x100 0x100 R E 0x4000\n'
        readelf = self.audit_with_readelf(apk, lambda *args, **kwargs: aligned)
        checked_segments = {Path(call.args[0][-1]).name for call in readelf.call_args_list
                            if call.args[0][1] == '-lW'}
        self.assertEqual(checked_segments, {Path(name).name for name in native})

    def test_stored_native_library_at_4k_offset_is_rejected(self):
        extra = 'lib/arm64-v8a/libtensorflowlite_jni.so'
        self.entries[extra] = b'fixture'
        native = {name: (4096 if name == extra else 0)
                  for name in self.entries if name.endswith('.so')}
        apk = self.stored_apk(native)
        aligned = '  LOAD 0x000000 0x000000 0x000000 0x100 0x100 R E 0x4000\n'
        with self.assertRaisesRegex(ValueError, '16 KiB'):
            self.audit_with_readelf(apk, lambda *args, **kwargs: aligned)

    def test_stored_required_library_at_4k_offset_is_rejected(self):
        required = 'lib/arm64-v8a/libc++_shared.so'
        native = {name: (4096 if name == required else 0)
                  for name in self.entries if name.endswith('.so')}
        apk = self.stored_apk(native)
        aligned = '  LOAD 0x000000 0x000000 0x000000 0x100 0x100 R E 0x4000\n'
        with self.assertRaisesRegex(ValueError, '16 KiB'):
            self.audit_with_readelf(apk, lambda *args, **kwargs: aligned)

    def test_additional_native_library_bad_load_alignment_is_rejected(self):
        extra = 'libtensorflowlite_jni.so'
        self.entries['lib/arm64-v8a/' + extra] = b'fixture'
        native = {name: 0 for name in self.entries if name.endswith('.so')}
        apk = self.stored_apk(native)
        aligned = '  LOAD 0x000000 0x000000 0x000000 0x100 0x100 R E 0x4000\n'
        misaligned = '  LOAD 0x000000 0x000000 0x000000 0x100 0x100 R E 0x1000\n'
        seen = []

        def readelf_output(command, **kwargs):
            if command[1] == '-lW':
                seen.append(Path(command[-1]).name)
                return misaligned if Path(command[-1]).name == extra else aligned
            return aligned

        with self.assertRaisesRegex(ValueError, '16 KiB'):
            self.audit_with_readelf(apk, readelf_output)
        self.assertIn(extra, seen)

    def test_additional_native_library_bad_machine_is_rejected(self):
        self.entries['lib/arm64-v8a/libtensorflowlite_jni.so'] = b'fixture'
        apk = self.stored_apk({name: 0 for name in self.entries if name.endswith('.so')})
        aligned = '  LOAD 0x000000 0x000000 0x000000 0x100 0x100 R E 0x4000\n'

        def readelf_output(command, **kwargs):
            if command[1] == '-hW' and Path(command[-1]).name == 'libtensorflowlite_jni.so':
                return 'Type: DYN (Shared object file)\nMachine: Advanced Micro Devices X86-64\n'
            return self.mock_readelf(command, aligned)

        with patch.object(audit, 'check'), patch.object(audit.subprocess, 'check_output',
                                                     side_effect=readelf_output):
            with self.assertRaisesRegex(ValueError, 'Expected arm64-v8a shared object'):
                audit.main(apk, 'arm64-v8a', self.root / 'readelf', self.root)

    def test_unexpected_abi_library_is_rejected(self):
        self.entries['lib/x86_64/libextra.so'] = b'fixture'
        with self.assertRaisesRegex(ValueError, 'Unexpected APK native ABI'):
            self.verify()

    def test_nested_native_library_path_is_rejected(self):
        self.entries['lib/arm64-v8a/nested/libextra.so'] = b'fixture'
        with self.assertRaisesRegex(ValueError, 'Unexpected native library path'):
            self.verify()

    def test_drive_qualified_native_library_path_is_rejected_before_extraction(self):
        unsafe_name = 'Z:libextra.so'
        self.entries['lib/arm64-v8a/' + unsafe_name] = b'fixture'
        apk = self.stored_apk({name: 0 for name in self.entries if name.endswith('.so')})
        original_write_bytes = Path.write_bytes
        original_mkdir = Path.mkdir

        def guarded_mkdir(path, *args, **kwargs):
            if str(path) == 'Z:':
                return None
            return original_mkdir(path, *args, **kwargs)

        def guarded_write_bytes(path, data):
            if unsafe_name in str(path):
                raise AssertionError('Unsafe native library extraction was attempted')
            return original_write_bytes(path, data)

        with patch.object(Path, 'mkdir', guarded_mkdir), patch.object(Path, 'write_bytes', guarded_write_bytes):
            with self.assertRaisesRegex(ValueError, 'Unexpected native library path'):
                self.audit_with_readelf(apk, lambda *args, **kwargs:
                                        '  LOAD 0x000000 0x000000 0x000000 0x100 0x100 R E 0x4000\n')

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
