"""Small, network-free producer, fixture and audit regressions."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

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

    def test_prepare_parent_is_caller_owned_and_must_already_exist(self):
        missing_parent = self.root / "generated"
        with self.assertRaises(FileNotFoundError):
            probe.prepare(missing_parent / "reply-asr-component")
        self.assertFalse(missing_parent.exists())

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

    def test_vad_model_pin_notice_and_fetch_destination_are_frozen(self):
        pin = probe.PINS["vad_model"]
        self.assertEqual(pin["repository"], "ggml-org/whisper-vad")
        self.assertEqual(pin["revision"], "9ffd54a1e1ee413ddf265af9913beaf518d1639b")
        self.assertEqual(pin["file"], "ggml-silero-v6.2.0.bin")
        self.assertEqual(pin["bytes"], 885098)
        self.assertEqual(pin["sha256"],
                         "2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987")
        self.assertEqual(pin["url"],
                         "https://huggingface.co/ggml-org/whisper-vad/resolve/"
                         "9ffd54a1e1ee413ddf265af9913beaf518d1639b/ggml-silero-v6.2.0.bin")
        self.assertEqual(probe.model_path(Path("stage"), pin),
                         Path("stage/assets/reply-asr/ggml-silero-v6.2.0.bin"))
        notice = probe.PINS["notices"]["SILERO-MIT.txt"]
        self.assertEqual(notice["bytes"], 1075)
        self.assertEqual(notice["sha256"],
                         "2e63e9a38b6e8fc0c7bc37ce174caca1862870856c6daf5697cfb785e925520b")
        self.assertEqual(notice["url"],
                         "https://raw.githubusercontent.com/snakers4/silero-vad/"
                         "0dd45f0bcd7271463c234f3bae5ad25181f9df8b/LICENSE")
        attribution = (probe.VENDOR / "ATTRIBUTION.txt").read_text(encoding="utf-8")
        self.assertIn("Silero VAD v6.2 weights", attribution)
        self.assertNotIn("No Silero weights", attribution)

    def test_host_smoke_passes_two_independent_borrowed_model_spans(self):
        stage = self.root / "stage"
        assets = stage / "assets/reply-asr"
        evidence = self.root / "evidence"
        fixtures = self.root / "fixtures"
        assets.mkdir(parents=True)
        evidence.mkdir()
        fixtures.mkdir()
        (fixtures / "NOTICES.txt").write_text("fixture notice", encoding="utf-8")
        model = {"file": "model.bin", "bytes": 4,
                 "sha256": hashlib.sha256(b"GGML").hexdigest()}
        vad = {"file": "vad.bin", "bytes": 4,
               "sha256": hashlib.sha256(b"VAD!").hexdigest()}
        (assets / model["file"]).write_bytes(b"GGML")
        (assets / vad["file"]).write_bytes(b"VAD!")
        pins = {**probe.PINS, "model": model, "vad_model": vad,
                "fixtures": [{"file": "a"}, {"file": "b"}]}
        completed = mock.Mock(returncode=0)
        with mock.patch.object(probe, "PINS", pins), \
             mock.patch.object(probe, "FIXTURES", fixtures), \
             mock.patch.object(probe, "fixture_pcm", side_effect=(b"\0\0", b"\0\0")), \
             mock.patch.object(probe.subprocess, "run", return_value=completed) as run:
            probe.run_smoke(Path("wrapper-smoke"), Path("vad-probe"), stage, evidence)

        wrapper = run.call_args_list[0]
        command = wrapper.args[0]
        self.assertEqual(command[0], "wrapper-smoke")
        self.assertEqual(command[2:4], [str(len(b"non-model-prefix!")), "4"])
        self.assertEqual(command[5:7], [str(len(b"non-vad-prefix!")), "4"])
        self.assertEqual(len(command), 9)
        self.assertNotEqual(command[1], command[4])
        self.assertEqual(wrapper.kwargs["pass_fds"], (int(command[1]), int(command[4])))
        log = (evidence / "host-smoke.txt").read_text(encoding="utf-8")
        self.assertIn("padded-model-and-vad-spans SHA-256 pass", log)
        self.assertIn("shifted spans SHA-256 reject", log)

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


    def test_model_load_cleanup_requires_exact_source_and_result_hashes(self):
        old = (b'        WHISPER_LOG_ERROR("%s: failed to load model\\n", __func__);'
               b"\n        delete ctx;\n        return nullptr;")
        fixed = old.replace(b"delete ctx;", b"whisper_free(ctx);")
        source = b"prefix\n" + old + b"\nsuffix\n"
        repaired = b"prefix\n" + fixed + b"\nsuffix\n"
        pin = {"original_sha256": hashlib.sha256(source).hexdigest(),
               "repaired_sha256": hashlib.sha256(repaired).hexdigest()}
        self.assertEqual(probe.repair_whisper_cleanup_bytes(source, pin), repaired)
        with self.assertRaises(ValueError):
            probe.repair_whisper_cleanup_bytes(source + b"unexpected", pin)
        with self.assertRaises(ValueError):
            probe.repair_whisper_cleanup_bytes(source, {**pin, "repaired_sha256": "0" * 64})
        duplicated = source + old
        with self.assertRaises(ValueError):
            probe.repair_whisper_cleanup_bytes(
                duplicated, {"original_sha256": hashlib.sha256(duplicated).hexdigest(),
                             "repaired_sha256": hashlib.sha256(duplicated).hexdigest()})

    def test_vad_init_lifetime_fails_before_and_repairs_old_prepared_source(self):
        old_prepared = (
            b"#include <map>\n#include <random>\n"
            b"    whisper_vad_context * vctx = new whisper_vad_context;\n"
            b"    vctx->n_threads = params.n_threads;\n"
            b"    if (!whisper_vad_init_context(vctx)) {\n"
            b"        whisper_vad_free(vctx);\n"
            b"        return nullptr;\n"
            b"    }\n\n"
            b"    return vctx;\n"
        )
        expected = (
            b"#include <map>\n#include <memory>\n#include <random>\n"
            b"    whisper_vad_context * vctx = new whisper_vad_context{};\n"
            b"    std::unique_ptr<whisper_vad_context, decltype(&whisper_vad_free)> "
            b"vctx_guard(vctx, whisper_vad_free);\n"
            b"    vctx->n_threads = params.n_threads;\n"
            b"    if (!whisper_vad_init_context(vctx)) {\n"
            b"        return nullptr;\n"
            b"    }\n\n"
            b"    return vctx_guard.release();\n"
        )
        pin = {"original_sha256": hashlib.sha256(old_prepared).hexdigest(),
               "repaired_sha256": hashlib.sha256(expected).hexdigest()}
        self.assertNotIn(b"vctx_guard", old_prepared)
        self.assertEqual(probe.repair_vad_init_lifetime_bytes(old_prepared, pin), expected)
        with self.assertRaises(ValueError):
            probe.repair_vad_init_lifetime_bytes(old_prepared + b"unexpected", pin)
        with self.assertRaises(ValueError):
            probe.repair_vad_init_lifetime_bytes(
                old_prepared, {**pin, "repaired_sha256": "0" * 64})

        source = probe.PINS["source"]
        self.assertEqual(source["model_load_cleanup_repair"]["repaired_sha256"],
                         source["vad_init_lifetime_repair"]["original_sha256"])
        self.assertEqual(source["vad_init_lifetime_repair"]["repaired_sha256"],
                         "576b8513b6679aa5fe66a7e414a565af81f51a373d5807838a98dc0a85900aa5")

    def test_vad_compute_failure_fails_before_and_repairs_old_prepared_source(self):
        old_prepared = (
            b"    const int64_t t_start_vad_us = ggml_time_us();\n\n"
            b"    for (int i = 0; i < n_chunks; i++) {\n"
            b"        if (!ggml_graph_compute_helper(sched, gf, vctx->n_threads, false)) {\n"
            b"            WHISPER_LOG_ERROR(\"%s: failed to compute VAD graph\\n\", __func__);\n"
            b"            break;\n"
            b"        }\n"
            b"    }\n\n"
            b"    ggml_backend_sched_reset(sched);\n\n"
            b"    return true;\n"
            b"}\n\n"
            b"bool whisper_vad_detect_speech(\n"
        )
        expected = (
            b"    const int64_t t_start_vad_us = ggml_time_us();\n"
            b"    bool compute_ok = true;\n\n"
            b"    for (int i = 0; i < n_chunks; i++) {\n"
            b"        if (!ggml_graph_compute_helper(sched, gf, vctx->n_threads, false)) {\n"
            b"            WHISPER_LOG_ERROR(\"%s: failed to compute VAD graph\\n\", __func__);\n"
            b"            compute_ok = false;\n"
            b"            break;\n"
            b"        }\n"
            b"    }\n\n"
            b"    ggml_backend_sched_reset(sched);\n\n"
            b"    return compute_ok;\n"
            b"}\n\n"
            b"bool whisper_vad_detect_speech(\n"
        )
        pin = {"original_sha256": hashlib.sha256(old_prepared).hexdigest(),
               "repaired_sha256": hashlib.sha256(expected).hexdigest()}
        self.assertEqual(probe.repair_vad_compute_failure_bytes(old_prepared, pin), expected)
        self.assertLess(expected.index(b"ggml_backend_sched_reset(sched)"),
                        expected.index(b"return compute_ok"))
        with self.assertRaises(ValueError):
            probe.repair_vad_compute_failure_bytes(old_prepared + b"unexpected", pin)
        with self.assertRaises(ValueError):
            probe.repair_vad_compute_failure_bytes(
                old_prepared, {**pin, "repaired_sha256": "0" * 64})

        source = probe.PINS["source"]
        self.assertEqual(source["vad_init_lifetime_repair"]["repaired_sha256"],
                         source["vad_compute_failure_repair"]["original_sha256"])
        self.assertEqual(source["vad_compute_failure_repair"]["repaired_sha256"],
                         "79f21daa732c073967d802725fa78d71100da0cdc34ef2fceae7e28dffa6cbd6")

    def test_vad_cancellation_repairs_header_and_callback_poll_boundary(self):
        cpp_before = (
            b"bool whisper_vad_detect_speech_no_reset(\n"
            b"        struct whisper_vad_context * vctx,\n"
            b"        const float * samples,\n"
            b"        int n_samples) {\n"
            b"        if (!ggml_graph_compute_helper(sched, gf, vctx->n_threads, false)) {\n"
            b"            WHISPER_LOG_ERROR(\"%s: failed to compute VAD graph\\n\", __func__);\n"
            b"            compute_ok = false;\n"
            b"            break;\n"
            b"        }\n\n"
            b"        // Get the probability for this chunk.\n"
            b"        ggml_backend_tensor_get(prob, &vctx->probs[i], 0, sizeof(float));\n"
            b"    ggml_backend_sched_reset(sched);\n\n"
            b"    return compute_ok;\n"
            b"}\n\n"
            b"bool whisper_vad_detect_speech(\n"
            b"        struct whisper_vad_context * vctx,\n"
            b"        const float * samples,\n"
            b"        int n_samples) {\n"
            b"    whisper_vad_reset_state(vctx);\n"
            b"    return whisper_vad_detect_speech_no_reset(vctx, samples, n_samples);\n"
            b"}\n"
        )
        cpp_pin = {
            "original_sha256": hashlib.sha256(cpp_before).hexdigest(),
            "repaired_sha256": "87420ad74a11e213dff632ccd5b5c54b85d1b7fa8ccbed5db576aca489f4705c",
        }
        cpp_after = probe.repair_vad_cancellation_cpp_bytes(cpp_before, cpp_pin)
        self.assertIn(b"static bool whisper_vad_detect_speech_no_reset_internal(", cpp_after)
        self.assertIn(b"ggml_abort_callback abort_callback", cpp_after)
        compute = cpp_after.index(b"ggml_graph_compute_helper")
        poll = cpp_after.index(b"abort_callback && abort_callback(abort_callback_user_data)")
        probability = cpp_after.index(b"ggml_backend_tensor_get(prob")
        self.assertLess(compute, poll)
        self.assertLess(poll, probability)
        self.assertIn(b"compute_ok = false;\n            break;", cpp_after[poll:probability])
        self.assertIn(
            b"return whisper_vad_detect_speech_no_reset_internal(vctx, samples, n_samples, nullptr, nullptr);",
            cpp_after,
        )
        self.assertIn(b"bool whisper_vad_detect_speech_with_abort(", cpp_after)
        self.assertLess(cpp_after.index(b"ggml_backend_sched_reset(sched)"),
                        cpp_after.index(b"return compute_ok"))
        with self.assertRaises(ValueError):
            probe.repair_vad_cancellation_cpp_bytes(cpp_before + b"unexpected", cpp_pin)
        with self.assertRaises(ValueError):
            probe.repair_vad_cancellation_cpp_bytes(
                cpp_before, {**cpp_pin, "repaired_sha256": "0" * 64})

        header_before = (
            b"    WHISPER_API bool whisper_vad_detect_speech(\n"
            b"            struct whisper_vad_context * vctx,\n"
            b"                           const float * samples,\n"
            b"                                   int   n_samples);\n"
        )
        header_pin = {
            "original_sha256": hashlib.sha256(header_before).hexdigest(),
            "repaired_sha256": "f8a5552056ec49d13504bbadd470c1ace330fd7a706e5005cb3277a02fadf671",
        }
        header_after = probe.repair_vad_cancellation_header_bytes(header_before, header_pin)
        self.assertEqual(header_after.count(b"whisper_vad_detect_speech_with_abort"), 1)
        self.assertIn(b"ggml_abort_callback abort_callback", header_after)
        with self.assertRaises(ValueError):
            probe.repair_vad_cancellation_header_bytes(header_before + b"unexpected", header_pin)
        with self.assertRaises(ValueError):
            probe.repair_vad_cancellation_header_bytes(
                header_before, {**header_pin, "repaired_sha256": "0" * 64})

        source = probe.PINS["source"]
        cancellation = source["vad_cancellation_repair"]
        self.assertEqual(source["vad_compute_failure_repair"]["repaired_sha256"],
                         cancellation["implementation"]["original_sha256"])
        self.assertEqual(cancellation["implementation"]["repaired_sha256"],
                         "7f867a2cfa0e5fca3d98fcc41379be87c5393d30130d6a777e3df60109add68c")
        self.assertEqual(cancellation["header"]["original_sha256"],
                         "a7d19f7feb5be52426628ff07e0602de28a30dc4312d0d0603e1e536753f76dd")
        self.assertEqual(cancellation["header"]["repaired_sha256"],
                         "89dbdf9bd5415b5ed36446063eca9962fb4cf7580c3a3e20c1da8e1b8e90a125")


class LeakGatePolicyTest(unittest.TestCase):
    NATIVE = SCRIPT.parent.parent
    COMPONENT = NATIVE / "research/reply_asr_component"
    WORKFLOW = NATIVE.parent / ".github/workflows/sign-reply-component.yml"

    @classmethod
    def sources(cls):
        return {
            "model": (cls.COMPONENT / "model_load_leak_regression.cpp").read_bytes(),
            "vad": (cls.COMPONENT / "vad_model_load_leak_regression.cpp").read_text(),
            "compute": (cls.COMPONENT / "vad_compute_failure_regression.cpp").read_text(),
            "wrapper_compute": (
                cls.COMPONENT / "wrapper_vad_compute_failure_regression.cpp"
            ).read_text(),
            "vad_cancellation": (
                cls.COMPONENT / "vad_cancellation_regression.cpp"
            ).read_text(),
            "cmake": (cls.COMPONENT / "CMakeLists.txt").read_text(),
            "build": (cls.NATIVE / "scripts/build-reply-asr-component.sh").read_text(),
            "workflow": cls.WORKFLOW.read_text(),
        }

    def audit(self, source):
        self.assertEqual(
            hashlib.sha256(source["model"]).hexdigest(),
            "09315b59ef397c02e8b91cbb55af7917068d7a9b1167b0b13a8aa3e3a565e467",
        )
        self.assertIn("late-model-read-failure %s", source["model"].decode())
        self.assertNotIn("VAD_LATE_READ_LEAK_TRIGGERED", source["model"].decode())
        self.assertIn("STS_VAD_EARLY_METADATA_CLEANUP_PASS", source["vad"])
        self.assertIn("STS_VAD_LATE_READ_CLEANUP_PASS", source["vad"])
        self.assertIn("whisper_vad_init_with_params", source["vad"])
        self.assertIn("STS_VAD_COMPUTE_INJECTION wrapper_calls=", source["compute"])
        self.assertIn("STS_VAD_COMPUTE_FAILURE_PROPAGATION_PASS detect_return=false",
                      source["compute"])
        self.assertIn("if (detected)", source["compute"])
        self.assertIn("failure_status != STS_REPLY_DECODE_FAILED", source["wrapper_compute"])
        self.assertIn("output_cleared", source["wrapper_compute"])
        self.assertIn("retry_status != STS_REPLY_OK", source["wrapper_compute"])
        self.assertIn("position_is(model_fd, model_position)", source["wrapper_compute"])
        self.assertIn("injected_failures.compare_exchange_strong", source["wrapper_compute"])
        self.assertIn("cancelled_status != STS_REPLY_CANCELLED", source["vad_cancellation"])
        self.assertIn("calls != 1", source["vad_cancellation"])
        self.assertIn("STS_VAD_CANCELLATION_RED", source["vad_cancellation"])
        self.assertIn("STS_VAD_CANCELLATION_PASS", source["vad_cancellation"])
        self.assertIn("__wrap_whisper_vad_reset_state", source["vad_cancellation"])
        self.assertIn("__real_whisper_vad_reset_state(context)", source["vad_cancellation"])
        self.assertIn("__wrap_whisper_vad_probs", source["vad_cancellation"])
        self.assertIn("__real_whisper_vad_probs(context)", source["vad_cancellation"])
        self.assertIn("probabilities[0] = 0.5f", source["vad_cancellation"])
        self.assertIn("sentinel_injected", source["vad_cancellation"])
        self.assertIn("cleanup_reset_calls", source["vad_cancellation"])
        self.assertIn("probabilities[i] == 0.f", source["vad_cancellation"])

        self.assertIn(
            "add_executable(reply_asr_model_load_leak_regression model_load_leak_regression.cpp)",
            source["cmake"],
        )
        self.assertIn(
            "add_executable(reply_asr_vad_model_load_leak_regression "
            "vad_model_load_leak_regression.cpp)",
            source["cmake"],
        )
        self.assertIn(
            "add_executable(reply_asr_wrapper_vad_compute_failure_regression",
            source["cmake"],
        )
        self.assertIn("../../vendor/reply-asr/reply_asr.cpp", source["cmake"])
        self.assertIn(
            "add_executable(reply_asr_vad_cancellation_regression",
            source["cmake"],
        )
        self.assertIn("vad_cancellation_regression.cpp", source["cmake"])
        self.assertIn("-Wl,--wrap=ggml_backend_sched_graph_compute", source["cmake"])
        self.assertIn("-Wl,--wrap=whisper_vad_probs", source["cmake"])
        self.assertIn("-Wl,--wrap=whisper_vad_reset_state", source["cmake"])

        build = source["build"]
        self.assertIn('whisper_source="$stage/whisper.cpp"', build)
        final_source_sha256 = (
            "7f867a2cfa0e5fca3d98fcc41379be87c5393d30130d6a777e3df60109add68c"
        )
        self.assertEqual(build.count(final_source_sha256), 3)
        final_header_sha256 = (
            "89dbdf9bd5415b5ed36446063eca9962fb4cf7580c3a3e20c1da8e1b8e90a125"
        )
        self.assertEqual(build.count(final_header_sha256), 3)
        self.assertEqual(build.count('"$whisper_source/include/whisper.h"'), 8)
        self.assertNotIn(
            "79f21daa732c073967d802725fa78d71100da0cdc34ef2fceae7e28dffa6cbd6",
            build,
        )
        self.assertNotIn(
            "576b8513b6679aa5fe66a7e414a565af81f51a373d5807838a98dc0a85900aa5",
            build,
        )
        self.assertIn('targets=(reply_asr_vad_compute_failure_regression)', build)
        self.assertIn('"$evidence/vad-compute-failure-source-sha256.txt"', build)
        self.assertIn(
            'targets=(reply_asr_wrapper_vad_compute_failure_regression)', build
        )
        self.assertIn('"$evidence/wrapper-vad-compute-failure-source-sha256.txt"', build)
        self.assertIn("STS_WRAPPER_VAD_COMPUTE_MAPPING_PASS status=5", build)
        self.assertIn('targets=(reply_asr_vad_cancellation_regression)', build)
        self.assertIn('"$evidence/vad-cancellation-source-sha256.txt"', build)
        self.assertIn('"$evidence/vad-cancellation-regression.txt"', build)
        self.assertIn("STS_VAD_CANCELLATION_GREEN status=3 scheduler_calls=1", build)
        self.assertIn(
            "STS_VAD_CANCELLATION_CLEANUP reset_calls=[1-9][0-9]* "
            "sentinel_injected=1",
            build,
        )
        self.assertIn("STS_VAD_CANCELLATION_EXPECTED_RED status=3 scheduler_calls=%s", build)
        self.assertIn('[[ "$status" -eq 1 && "$calls" -gt 1 ]]', build)
        self.assertIn('targets=(reply_asr_model_load_leak_regression)', build)
        self.assertIn('model="$stage/assets/reply-asr/ggml-tiny.en.bin"', build)
        self.assertIn("late-model-read-failure pass", build)
        self.assertIn('"$evidence/model-load-leak-regression.txt"', build)
        self.assertIn('"$evidence/model-load-leak-source-sha256.txt"', build)

        self.assertIn('elif [[ "$role" == host-vad-leak-regression ]]; then', build)
        self.assertIn('targets=(reply_asr_vad_model_load_leak_regression)', build)
        self.assertNotIn("whisper.cpp-v1.9.5-pristine", build)
        self.assertIn('"$evidence/vad-leak-source-sha256.txt"', build)
        self.assertIn('"$evidence/vad-model-load-leak-regression.txt"', build)
        self.assertIn("STS_VAD_INIT_LIFETIME_REPAIRED sanitizer_status=0", build)
        self.assertIn("STS_VAD_EARLY_METADATA_CLEANUP_PASS reads=9 close_calls=1", build)
        self.assertIn("STS_VAD_LATE_READ_CLEANUP_PASS reads=8 bytes=99238 close_calls=1", build)
        self.assertNotIn("STS_EXPECTED_RED", build)

        workflow = source["workflow"]
        for name, role in (
            ("Reject VAD graph-compute failure as valid silence",
             "host-vad-compute-failure-regression"),
            ("Map wrapper VAD graph-compute failure and recover",
             "host-wrapper-vad-compute-failure-regression"),
            ("Cancel during VAD before Whisper polling",
             "host-vad-cancellation-regression"),
            ("Verify repaired Whisper model-load cleanup", "host-leak-regression"),
            ("Verify repaired VAD initialization lifetime", "host-vad-leak-regression"),
        ):
            gate = (
                f"      - name: {name}\n"
                "        if: ${{ !cancelled() && steps.prepare.outcome == 'success' }}\n"
                "        timeout-minutes: 4\n"
                f"        run: bash native/scripts/build-reply-asr-component.sh {role}"
            )
            self.assertIn(gate, workflow)
        self.assertNotIn("continue-on-error", workflow)

    def test_repaired_whisper_and_vad_gates_are_independent(self):
        self.audit(self.sources())

    def test_policy_rejects_overwrite_source_confusion_and_masking(self):
        original = self.sources()
        mutations = []
        copy = dict(original)
        copy["model"] = original["vad"].encode()
        mutations.append(copy)
        copy = dict(original)
        copy["wrapper_compute"] = original["wrapper_compute"].replace(
            "failure_status != STS_REPLY_DECODE_FAILED",
            "failure_status != STS_REPLY_NO_SPEECH",
        )
        mutations.append(copy)
        copy = dict(original)
        copy["build"] = original["build"].replace(
            'model="$stage/assets/reply-asr/ggml-tiny.en.bin"',
            'model="$stage/assets/reply-asr/ggml-silero-v6.2.0.bin"',
        )
        mutations.append(copy)
        copy = dict(original)
        copy["build"] = original["build"].replace(
            "STS_VAD_INIT_LIFETIME_REPAIRED sanitizer_status=0",
            "STS_VAD_INIT_LIFETIME_UNVERIFIED sanitizer_status=0",
        )
        mutations.append(copy)
        copy = dict(original)
        copy["build"] = original["build"].replace(
            '[[ "$status" -eq 1 && "$calls" -gt 1 ]]',
            '[[ "$status" -eq 1 && "$calls" -gt 0 ]]',
        )
        mutations.append(copy)
        copy = dict(original)
        copy["workflow"] = original["workflow"].replace(
            "        run: bash native/scripts/build-reply-asr-component.sh host-vad-leak-regression",
            "        continue-on-error: true\n"
            "        run: bash native/scripts/build-reply-asr-component.sh host-vad-leak-regression",
        )
        mutations.append(copy)
        for mutation in mutations:
            with self.subTest(index=mutations.index(mutation)), self.assertRaises(AssertionError):
                self.audit(mutation)


if __name__ == "__main__":
    unittest.main()
