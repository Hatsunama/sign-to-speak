import hashlib
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zipfile

spec = importlib.util.spec_from_file_location(
    "reply_apk", Path(__file__).with_name("check-reply-apk-integration.py"))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def dex(classes):
    count = len(classes)
    strings, types, definitions = 112, 112 + 4 * count, 112 + 8 * count
    blob = bytearray(112 + 40 * count)
    blob[:8] = b"dex\n035\x00"
    struct.pack_into("<II", blob, 36, 112, 0x12345678)
    struct.pack_into("<IIII", blob, 56, count, strings, count, types)
    struct.pack_into("<II", blob, 96, count, definitions)
    for index, name in enumerate(classes):
        struct.pack_into("<I", blob, strings + 4 * index, len(blob))
        struct.pack_into("<I", blob, types + 4 * index, index)
        struct.pack_into("<I", blob, definitions + 32 * index, index)
        blob += bytes([len(name)]) + name + b"\0"
    struct.pack_into("<I", blob, 32, len(blob))
    return bytes(blob)


class ReplyApkIntegrationGateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = self.root / "manifest.xml"
        self.xml = '''<manifest xmlns:android="http://schemas.android.com/apk/res/android"
          package="com.xmilo_at_your_side.sign_to_speak.qa">
          <uses-permission android:name="android.permission.CAMERA"/>
          <uses-permission android:name="android.permission.RECORD_AUDIO"/>
          <uses-permission android:name="com.xmilo_at_your_side.sign_to_speak.qa.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"/>
          <application android:allowAudioPlaybackCapture="false" android:allowBackup="false"
            android:usesCleartextTraffic="false"/>
        </manifest>'''
        self.manifest.write_text(self.xml)
        self.models = {"assets/reply-asr/ggml-tiny.en.bin": b"whisper fixture",
                       "assets/reply-asr/ggml-silero-v6.2.0.bin": b"vad fixture"}
        self.notices = {"assets/reply-asr-notices/WHISPER-CPP-MIT.txt": b"license fixture",
                        "assets/reply-asr-notices/ATTRIBUTION.txt": b"attribution fixture"}
        self.entries = {**self.models, **self.notices,
                        "classes.dex": dex([gate.MODULE_CLASS, gate.REQUEST_CLASS]),
                        "lib/arm64-v8a/libsign_reply_asr.so": b"runtime fixture",
                        "lib/arm64-v8a/libprivate_reply_asr.so": b"jni fixture"}
        pins = {name: (len(data), hashlib.sha256(data).hexdigest())
                for name, data in self.models.items()}
        notice_pins = {name: (len(data), hashlib.sha256(data).hexdigest())
                       for name, data in self.notices.items()}
        for attribute, value in (("MODEL_ASSETS", pins), ("NOTICE_ASSETS", notice_pins)):
            patcher = mock.patch.object(gate, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.apk = self.root / "app.apk"

    def write_apk(self, compressed=()):
        with zipfile.ZipFile(self.apk, "w") as archive:
            for name, data in self.entries.items():
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_DEFLATED if name in compressed else zipfile.ZIP_STORED
                archive.writestr(info, data)

    @staticmethod
    def readelf(command, **_kwargs):
        flag, name = command[1], Path(command[-1]).name
        if flag == "-hW":
            return "Type: DYN (Shared object file)\nMachine: AArch64\n"
        if flag == "-lW":
            return "  LOAD 0x000000 0x000000 0x000000 0x100 0x100 R E 0x4000\n"
        if flag == "-dW":
            needed = "libsign_reply_asr.so" if name == "libprivate_reply_asr.so" else "libc++_shared.so"
            return f"(NEEDED) Shared library: [{needed}]\n(SONAME) Library soname: [{name}]\n"
        exports = gate.JNI_EXPORTS if name == "libprivate_reply_asr.so" else gate.RUNTIME_EXPORTS
        return "Symbol table '.dynsym' contains entries:\n" + "".join(
            f"  1: 0 1 FUNC GLOBAL DEFAULT 1 {item}\n" for item in sorted(exports))

    def verify(self, compressed=()):
        self.write_apk(compressed)
        with mock.patch.object(gate.subprocess, "check_output", side_effect=self.readelf):
            gate.verify(self.manifest, self.apk, "arm64-v8a", self.root / "readelf", self.root / "evidence")

    def test_exact_packaged_contract_passes(self):
        self.verify()

    def test_reply_request_must_be_a_defined_dex_class(self):
        self.entries["classes.dex"] = dex([gate.MODULE_CLASS])
        with self.assertRaisesRegex(ValueError, "request class absent"):
            self.verify()

    def test_jni_exports_belong_to_reply_asr_request(self):
        self.assertEqual(gate.JNI_EXPORTS, {
            "Java_com_xmilo_1at_1your_1side_sign_1to_1speak_ReplyAsrRequest_nativeOpen",
            "Java_com_xmilo_1at_1your_1side_sign_1to_1speak_ReplyAsrRequest_nativeDecode",
            "Java_com_xmilo_1at_1your_1side_sign_1to_1speak_ReplyAsrRequest_nativeCancel",
            "Java_com_xmilo_1at_1your_1side_sign_1to_1speak_ReplyAsrRequest_nativeClose",
        })

    def test_reviewed_shared_component_and_jni_libraries_are_required(self):
        del self.entries["lib/arm64-v8a/libsign_reply_asr.so"]
        with self.assertRaisesRegex(ValueError, "libraries absent"):
            self.verify()

    def test_all_defined_default_visible_dynamic_symbols_are_audited(self):
        symbols = """Symbol table '.dynsym' contains entries:
  1: 0 1 FUNC GLOBAL DEFAULT 1 expected_function
  2: 0 8 OBJECT WEAK DEFAULT 2 leaked_object
  3: 0 8 OBJECT GLOBAL HIDDEN 2 hidden_object
  4: 0 0 NOTYPE WEAK DEFAULT UND imported_name
"""
        self.assertEqual(gate.dynamic_exports(symbols), {"expected_function", "leaked_object"})

    def test_current_manifest_without_microphone_permission_is_red(self):
        self.manifest.write_text(self.xml.replace(
            '          <uses-permission android:name="android.permission.RECORD_AUDIO"/>\n', ""))
        with self.assertRaisesRegex(ValueError, "microphone/no-network"):
            self.verify()

    def test_network_permission_is_red(self):
        self.manifest.write_text(self.xml.replace("</manifest>",
            '<uses-permission android:name="android.permission.INTERNET"/></manifest>'))
        with self.assertRaisesRegex(ValueError, "microphone/no-network|network-free"):
            self.verify()

    def test_models_require_exact_inventory_size_and_hash(self):
        name = next(iter(self.models))
        original = self.entries.pop(name)
        with self.assertRaisesRegex(ValueError, "model APK inventory"):
            self.verify()
        self.entries[name] = original + b"changed"
        with self.assertRaisesRegex(ValueError, "not exact|SHA-256"):
            self.verify()

    def test_models_must_be_uncompressed_for_asset_file_descriptor(self):
        name = next(iter(self.models))
        with self.assertRaisesRegex(ValueError, "descriptor-accessible"):
            self.verify([name])

    def test_notices_require_exact_inventory_and_bytes(self):
        name = next(iter(self.notices))
        original = self.entries.pop(name)
        with self.assertRaisesRegex(ValueError, "notice APK inventory"):
            self.verify()
        self.entries[name] = original + b"changed"
        with self.assertRaisesRegex(ValueError, "notice is not exact|SHA-256"):
            self.verify()

    def test_extra_reply_notice_is_rejected(self):
        self.entries["assets/reply-asr-notices/extra.txt"] = b"unexpected"
        with self.assertRaisesRegex(ValueError, "notice APK inventory"):
            self.verify()

    def test_current_apk_without_reply_libraries_is_red(self):
        for name in [name for name in self.entries if name.endswith(".so")]:
            del self.entries[name]
        with self.assertRaisesRegex(ValueError, "libraries absent"):
            self.verify()

    def test_wrong_abi_library_is_red(self):
        name = "lib/arm64-v8a/libsign_reply_asr.so"
        self.entries[name.replace("arm64-v8a", "x86_64")] = self.entries.pop(name)
        with self.assertRaisesRegex(ValueError, "unexpected ABI"):
            self.verify()

    def test_bridge_must_be_a_defined_dex_class(self):
        incidental = bytearray(dex([]))
        incidental += gate.MODULE_CLASS
        struct.pack_into("<I", incidental, 32, len(incidental))
        self.entries["classes.dex"] = bytes(incidental)
        with self.assertRaisesRegex(ValueError, "bridge class absent"):
            self.verify()

    def test_runtime_and_jni_exports_are_exact(self):
        for target in ("libsign_reply_asr.so", "libprivate_reply_asr.so"):
            def changed(command, **kwargs):
                data = self.readelf(command, **kwargs)
                return data.replace("Symbol table '.dynsym' contains entries:\n",
                                    "Symbol table '.dynsym' contains entries:\n  9: 0 1 FUNC GLOBAL DEFAULT 1 extra_export\n") \
                    if command[1] == "-Ws" and Path(command[-1]).name == target else data
            self.write_apk()
            with self.subTest(target=target), mock.patch.object(
                    gate.subprocess, "check_output", side_effect=changed), self.assertRaisesRegex(
                    ValueError, "ELF exports differ"):
                gate.verify(self.manifest, self.apk, "arm64-v8a", self.root / "readelf", self.root / "evidence")

    def test_jni_must_link_packaged_runtime(self):
        def unlinked(command, **kwargs):
            data = self.readelf(command, **kwargs)
            return data.replace("libsign_reply_asr.so", "libc++_shared.so") \
                if command[1] == "-dW" and Path(command[-1]).name == "libprivate_reply_asr.so" else data
        self.write_apk()
        with mock.patch.object(gate.subprocess, "check_output", side_effect=unlinked), \
                self.assertRaisesRegex(ValueError, "ELF linkage differs"):
            gate.verify(self.manifest, self.apk, "arm64-v8a", self.root / "readelf", self.root / "evidence")


if __name__ == "__main__":
    unittest.main()
