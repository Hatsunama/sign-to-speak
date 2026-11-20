"""Small, network-free producer, fixture and audit regressions."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("reply-asr-component.py")
spec = importlib.util.spec_from_file_location("reply_asr_component", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ProducerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(os.environ.get('SIGN_REPLY_TEST_TMP', SCRIPT.parent)))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pinned = {"bytes": 4, "sha256": hashlib.sha256(b"GGML").hexdigest()}

    def test_stream_only_publishes_matching_bytes(self):
        for payload in (b"", b"MLGG", b"GGMLplus"):
            with self.subTest(payload=payload):
                output = self.root / "model.bin"
                with self.assertRaises(ValueError):
                    probe.write_verified(io.BytesIO(payload), output, self.pinned)
                self.assertEqual(list(self.root.iterdir()), [])
        probe.write_verified(io.BytesIO(b"GGML"), output, self.pinned)
        self.assertEqual(output.read_bytes(), b"GGML")

    def test_partial_stream_failure_preserves_no_destination(self):
        class Interrupted(io.BytesIO):
            def read(self, size):
                if self.tell():
                    raise OSError("synthetic midstream read failure")
                return super().read(2)
        with self.assertRaisesRegex(OSError, "midstream"):
            probe.write_verified(Interrupted(b"GGML"), self.root / "model.bin", self.pinned)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_span_hash_rejects_malformed_model_and_out_of_bounds_offsets(self):
        address = self.root / "asset-span.bin"
        address.write_bytes(b"PRE" + b"GGML" + b"POST")
        with address.open("rb") as source:
            probe.verify_span(source, 3, 4, self.pinned)
            for offset, size in ((-1, 4), (2, 4), (3, 3), (3, 5), (8, 4)):
                with self.subTest(offset=offset, size=size), self.assertRaises(ValueError):
                    probe.verify_span(source, offset, size, self.pinned)
        address.write_bytes(b"PRE" + b"BAD!" + b"POST")
        with address.open("rb") as source, self.assertRaisesRegex(ValueError, "SHA-256"):
            probe.verify_span(source, 3, 4, self.pinned)

    def test_real_fixture_resampling_hash_and_all_pinned_notices(self):
        for pin, samples in zip(probe.PINS["fixtures"], (26880, 28160)):
            self.assertEqual(len(probe.fixture_pcm(pin)), samples * 2)
        for name, pin in {**probe.PINS["notices"], **probe.PINS["additional_notices"]}.items():
            with (probe.VENDOR / name).open("rb") as source:
                probe.consume_verified(source, pin)

    def test_existing_destination_never_replaced(self):
        output = self.root / "model.bin"
        output.write_bytes(b"other")
        with self.assertRaises(FileExistsError):
            probe.write_verified(io.BytesIO(b"GGML"), output, self.pinned)
        self.assertEqual(output.read_bytes(), b"other")

    def test_names_cannot_escape_notices_directory(self):
        for name in ("/absolute", "../outside", "a/b", "a\\b", ".", "a:stream"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                probe.output_name(name)

    def test_resampler_and_byte_order_are_frozen(self):
        self.assertEqual(probe.resample_24k([32767, 32767, -32768, -32768, 0, 32767]),
                         bytes.fromhex("ff7f55d5aaaa5455"))
        with self.assertRaises(ValueError):
            probe.resample_24k([0])

    def test_ndk_notice_accepts_actual_compiler_runtime_heading_and_rejects_missing_grants(self):
        grants = [
            (probe.NATIVE / "licenses/framework" / name).read_bytes()
            for name in ("ndk-libcxx-NOTICE.txt", "ndk-libcxxabi-NOTICE.txt")
        ]
        observed_heading = b"The compiler_rt library is dual licensed under both the University of Illinois"
        path = self.root / "ndk-NOTICE.toolchain"
        path.write_bytes(b"\n".join((*grants, observed_heading)))
        probe.verify_ndk_notice(self.root)
        for invalid in (b"\n".join(grants),
                        b"\n".join((grants[0], observed_heading)),
                        b"\n".join((grants[0], grants[0], grants[1], observed_heading))):
            with self.subTest(invalid_length=len(invalid)), self.assertRaises(ValueError):
                path.write_bytes(invalid)
                probe.verify_ndk_notice(self.root)

    def test_dynamic_loading_and_network_imports_are_rejected(self):
        probe.check_imports("U dlclose@LIBC\nU memcpy@LIBC")
        for symbol in ("dlopen", "dlsym", "connect", "socket", "getaddrinfo"):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                probe.check_imports(f"U {symbol}@LIBC")

    def test_elf_rejects_weak_exports_and_external_libraries(self):
        dynamic = "0x1 (NEEDED) Shared library: [libc.so]\n0xe (SONAME) Library soname: [libsign_reply_asr.so]"
        symbols = "00001000 T sts_reply_open\n00001010 T sts_reply_decode\n00001020 T sts_reply_close"
        probe.check_linkage(dynamic, symbols)
        with self.assertRaises(ValueError):
            probe.check_linkage(dynamic, symbols + "\n00001030 W whisper_load")
        with self.assertRaises(ValueError):
            probe.check_linkage(dynamic.replace("libc.so", "libcurl.so"), symbols)


    def test_pinned_ndk_unwinder_locations(self):
        ndk = self.root / "sdk" / "ndk" / "27.1.12297006"
        runtime = ndk / "toolchains/llvm/prebuilt/linux-x86_64/lib/clang/18/lib/linux"
        for abi, arch in (("arm64-v8a", "aarch64"), ("x86_64", "x86_64")):
            unwinder = runtime / arch / "libunwind.a"
            self.assertTrue(probe.pinned_ndk_archive(unwinder, abi, ndk))
            for wrong in (runtime / ("x86_64" if arch == "aarch64" else "aarch64") / "libunwind.a",
                          runtime / arch / "libunwind-extra.a",
                          ndk.parent / "27.1.12297005" / unwinder.relative_to(ndk),
                          self.root / "other" / unwinder.relative_to(ndk)):
                with self.subTest(abi=abi, wrong=str(wrong)):
                    self.assertFalse(probe.pinned_ndk_archive(wrong, abi, ndk))
        self.assertTrue(probe.pinned_ndk_archive(
            runtime / "libclang_rt.builtins-aarch64-android.a", "arm64-v8a", ndk))
        self.assertFalse(probe.pinned_ndk_archive(
            runtime / "libclang_rt.builtins-x86_64-android.a", "x86_64", ndk))


if __name__ == "__main__":
    unittest.main()
