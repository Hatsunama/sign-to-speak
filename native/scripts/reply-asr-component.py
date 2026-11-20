#!/usr/bin/env python3
"""Prepare pinned build inputs and verify standalone reply ASR component evidence."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import struct
import subprocess
import tempfile
import urllib.request
import wave

NATIVE = Path(__file__).resolve().parents[1]
VENDOR = NATIVE / "vendor/reply-asr"
PINS = json.loads((VENDOR / "pins.json").read_text(encoding="utf-8"))
FIXTURES = NATIVE.parent / "qa-artifacts/pocket-tts-audition-20261025"
CHUNK_SIZE = 1024 * 1024
EXPORTS = {"sts_reply_open", "sts_reply_decode", "sts_reply_close"}
PLATFORM_LIBRARIES = {"libc.so", "libm.so", "libdl.so", "liblog.so", "libc++_shared.so"}


def output_name(name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ValueError("unsafe output file name")
    return name


def reject_links(path):
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("symlink or reparse point in staging path")


def consume_verified(source, pin, output=None):
    size = pin["bytes"]
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        block = source.read(min(remaining, CHUNK_SIZE))
        if not block:
            raise ValueError("input shorter than pin")
        remaining -= len(block)
        digest.update(block)
        if output is not None:
            output.write(block)
    if source.read(1):
        raise ValueError("input larger than pin")
    if digest.hexdigest() != pin["sha256"]:
        raise ValueError("input SHA-256 differs from pin")


def write_verified(source, output, pin):
    reject_links(output)
    if output.exists():
        raise FileExistsError(output)
    with tempfile.TemporaryDirectory(dir=output.parent) as scratch:
        pending = Path(scratch) / "pending"
        with pending.open("wb") as destination:
            consume_verified(source, pin, destination)
        # Hard-link publishes atomically and fails if another writer used this name.
        os.link(pending, output)


def verify_span(source, offset, length, pin):
    info = os.fstat(source.fileno())
    if (not stat.S_ISREG(info.st_mode) or offset < 0 or length != pin["bytes"]
            or offset > info.st_size - length):
        raise ValueError("invalid model fd span")
    digest = hashlib.sha256()
    source.seek(offset)
    remaining = length
    while remaining:
        block = source.read(min(remaining, CHUNK_SIZE))
        if not block:
            raise ValueError("truncated model fd span")
        remaining -= len(block)
        digest.update(block)
    if digest.hexdigest() != pin["sha256"]:
        raise ValueError("model fd span SHA-256 differs from pin")


def fetch_source(destination, pin):
    subprocess.run(["git", "init", "--quiet", str(destination)], check=True)
    git = ["git", "-C", str(destination)]
    subprocess.run(git + ["-c", "fetch.recurseSubmodules=false", "fetch", "--depth=1",
                          "--no-tags", pin["repo"], pin["commit"]], check=True, timeout=120)
    subprocess.run(git + ["-c", "core.autocrlf=false", "checkout", "--quiet", "--detach",
                          "FETCH_HEAD"], check=True)
    for suffix, expected in (("HEAD", pin["commit"]), ("HEAD^{tree}", pin["tree"])):
        actual = subprocess.check_output(git + ["rev-parse", suffix], text=True).strip()
        if actual != expected:
            raise ValueError("upstream commit or tree differs from pin")
    if subprocess.check_output(git + ["status", "--porcelain", "--untracked-files=all"]):
        raise ValueError("upstream source worktree is dirty")
    if (destination / ".gitmodules").exists():
        raise ValueError("unexpected upstream submodules")


def verify_notices(directory):
    expected = {**PINS["notices"], **PINS["additional_notices"]}
    if {path.name for path in directory.iterdir()} != set(expected):
        raise ValueError("unexpected or missing notice files")
    for name, pin in expected.items():
        path = directory / output_name(name)
        reject_links(path)
        with path.open("rb") as source:
            consume_verified(source, pin)


def repair_whisper_cleanup_bytes(source, pin):
    if hashlib.sha256(source).hexdigest() != pin["original_sha256"]:
        raise ValueError("Whisper cleanup source SHA-256 differs from pin")
    before = (b'        WHISPER_LOG_ERROR("%s: failed to load model\\n", __func__);'
              b"\n        delete ctx;\n        return nullptr;")
    after = before.replace(b"delete ctx;", b"whisper_free(ctx);")
    if source.count(before) != 1:
        raise ValueError("Whisper cleanup source branch differs from pin")
    repaired = source.replace(before, after)
    if hashlib.sha256(repaired).hexdigest() != pin["repaired_sha256"]:
        raise ValueError("Whisper cleanup repair SHA-256 differs from pin")
    return repaired


def repair_vad_init_lifetime_bytes(source, pin):
    if hashlib.sha256(source).hexdigest() != pin["original_sha256"]:
        raise ValueError("VAD init lifetime source SHA-256 differs from pin")
    transformations = (
        (b"#include <map>\n#include <random>",
         b"#include <map>\n#include <memory>\n#include <random>"),
        (b"    whisper_vad_context * vctx = new whisper_vad_context;\n"
         b"    vctx->n_threads = params.n_threads;",
         b"    whisper_vad_context * vctx = new whisper_vad_context{};\n"
         b"    std::unique_ptr<whisper_vad_context, decltype(&whisper_vad_free)> "
         b"vctx_guard(vctx, whisper_vad_free);\n"
         b"    vctx->n_threads = params.n_threads;"),
        (b"    if (!whisper_vad_init_context(vctx)) {\n"
         b"        whisper_vad_free(vctx);\n"
         b"        return nullptr;\n"
         b"    }\n\n"
         b"    return vctx;",
         b"    if (!whisper_vad_init_context(vctx)) {\n"
         b"        return nullptr;\n"
         b"    }\n\n"
         b"    return vctx_guard.release();"),
    )
    repaired = source
    for before, after in transformations:
        if repaired.count(before) != 1:
            raise ValueError("VAD init lifetime source branch differs from pin")
        repaired = repaired.replace(before, after)
    if hashlib.sha256(repaired).hexdigest() != pin["repaired_sha256"]:
        raise ValueError("VAD init lifetime repair SHA-256 differs from pin")
    return repaired


def model_path(stage, pin):
    return stage / "assets/reply-asr" / output_name(pin["file"])


def prepare(output):
    reject_links(output)
    if output.exists():
        raise FileExistsError(output)
    # All partial state stays in scratch until source, notices and model pass.
    with tempfile.TemporaryDirectory(dir=output.parent) as scratch:
        stage = Path(scratch) / "component"
        stage.mkdir()
        fetch_source(stage / "whisper.cpp", PINS["source"])
        repair = PINS["source"]["model_load_cleanup_repair"]
        repair_file = stage / "whisper.cpp" / repair["path"]
        repair_file.write_bytes(repair_whisper_cleanup_bytes(repair_file.read_bytes(), repair))
        vad_repair = PINS["source"]["vad_init_lifetime_repair"]
        if vad_repair["path"] != repair["path"]:
            raise ValueError("Whisper repairs unexpectedly target different files")
        repair_file.write_bytes(repair_vad_init_lifetime_bytes(repair_file.read_bytes(), vad_repair))
        notices = stage / "notices"
        notices.mkdir()
        for name, pin in {**PINS["notices"], **PINS["additional_notices"]}.items():
            with (VENDOR / output_name(name)).open("rb") as source:
                write_verified(source, notices / name, pin)
        verify_notices(notices)
        if (stage / "whisper.cpp/LICENSE").read_bytes() != (notices / "WHISPER-CPP-MIT.txt").read_bytes():
            raise ValueError("license differs from the verified source tree")
        assets = stage / "assets/reply-asr"
        assets.mkdir(parents=True)
        for model in (PINS["model"], PINS["vad_model"]):
            request = urllib.request.Request(model["url"], headers={"Accept-Encoding": "identity"})
            with urllib.request.urlopen(request, timeout=45) as source:
                write_verified(source, model_path(stage, model), model)
        (stage / "inputs.json").write_text(json.dumps(PINS, indent=2) + "\n", encoding="utf-8")
        stage.rename(output)


def resample_24k(samples):
    """Area-average complete blocks of three 24 kHz samples into two at 16 kHz."""
    if len(samples) < 3:
        raise ValueError("fixture too short to resample")
    output = bytearray()
    for i in range(0, len(samples) - 2, 3):
        left, middle, right = samples[i:i + 3]
        output.extend(struct.pack("<hh", (2 * left + middle) // 3, (middle + 2 * right) // 3))
    return bytes(output)


def fixture_pcm(pin):
    path = FIXTURES / output_name(pin["file"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != pin["sha256"]:
        raise ValueError("WAV fixture SHA-256 differs from pin")
    with wave.open(str(path), "rb") as wav:
        frames = wav.getnframes()
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (24000, 1, 2):
            raise ValueError("unexpected fixture WAV format")
        if not 0 < frames <= 30 * 24000:
            raise ValueError("fixture exceeds duration bound")
        data = wav.readframes(frames)
    pcm = resample_24k(struct.unpack(f"<{frames}h", data))
    digest = hashlib.sha256(pcm).hexdigest()
    if digest != pin["pcm_sha256"]:
        raise ValueError("resampled PCM SHA-256 differs from pin")
    return pcm


def run_smoke(binary, vad_binary, stage, evidence):
    model = PINS["model"]
    fixture_report = []
    with tempfile.TemporaryDirectory(dir=stage.parent) as scratch:
        scratch = Path(scratch)
        raw_files = []
        for index, pin in enumerate(PINS["fixtures"]):
            pcm = fixture_pcm(pin)
            out = scratch / f"{index}.pcm16le"
            out.write_bytes(pcm)
            raw_files.append(out)
            fixture_report.append({**pin, "samples": len(pcm) // 2})
        (evidence / "fixtures.json").write_text(
            json.dumps({"resampler": "24-to-16k rational area averaging v1, floor to PCM16LE",
                        "fixtures": fixture_report}, indent=2) + "\n", encoding="utf-8")
        shutil.copyfile(FIXTURES / "NOTICES.txt", evidence / "fixture-notices.txt")
        with (evidence / "host-smoke.txt").open("w", encoding="utf-8") as log:
            try:
                # Exercise a borrowed fd containing bytes outside the asset.
                composite = scratch / "asset-span.bin"
                prefix = b"non-model-prefix!"
                with composite.open("wb") as output, model_path(stage, model).open("rb") as source:
                    output.write(prefix)
                    shutil.copyfileobj(source, output)
                    output.write(b"non-model-suffix")
                with composite.open("rb") as model_file:
                    verify_span(model_file, len(prefix), model["bytes"], model)
                    try:
                        verify_span(model_file, len(prefix) - 1, model["bytes"], model)
                    except ValueError as error:
                        if str(error) != "model fd span SHA-256 differs from pin":
                            raise
                    else:
                        raise ValueError("model fd span unexpectedly accepted at the wrong offset")
                    log.write("padded-model-span SHA-256 pass; shifted-span SHA-256 reject\n")
                    log.flush()
                    smoke_result = subprocess.run(
                        [str(binary), str(model_file.fileno()), str(len(prefix)),
                         str(model["bytes"]), *map(str, raw_files)],
                        stdout=log, stderr=subprocess.STDOUT,
                        pass_fds=(model_file.fileno(),), timeout=180)
                    smoke_status = smoke_result.returncode
            except subprocess.TimeoutExpired:
                log.write("host-smoke ERROR timeout\n")
                smoke_status = 124
            except Exception as error:
                log.write(f"host-smoke ERROR setup={type(error).__name__}\n")
                smoke_status = 125
        # This independent semantic probe must run even when decoder smoke is red.
        with (evidence / "host-vad-semantic.txt").open("w", encoding="utf-8") as log:
            try:
                vad_result = subprocess.run(
                    [str(vad_binary), str(model_path(stage, PINS["vad_model"])),
                     *map(str, raw_files)], stdout=log, stderr=subprocess.STDOUT,
                    timeout=120)
                vad_status = vad_result.returncode
            except subprocess.TimeoutExpired:
                log.write("vad-probe ERROR timeout\n")
                vad_status = 124
            except OSError as error:
                log.write(f"vad-probe ERROR launch={type(error).__name__}\n")
                vad_status = 125
        if smoke_status:
            raise subprocess.CalledProcessError(smoke_status, str(binary))
        if vad_status:
            raise subprocess.CalledProcessError(vad_status, str(vad_binary))


def check_linkage(dynamic, symbols):
    needed = re.findall(r"\(NEEDED\).*?\[([^]]+)\]", dynamic)
    soname = re.findall(r"\(SONAME\).*?\[([^]]+)\]", dynamic)
    if not needed or set(needed) - PLATFORM_LIBRARIES:
        raise ValueError(f"unapproved ELF dependencies: {needed}")
    if soname != ["libsign_reply_asr.so"] or re.search(r"\((?:RPATH|RUNPATH)\)", dynamic):
        raise ValueError("unexpected ELF SONAME or runtime path")
    actual = {line.split()[-1] for line in symbols.splitlines() if line.strip()}
    if actual != EXPORTS:
        raise ValueError(f"unexpected ELF exports: {sorted(actual)}")


def check_imports(imports):
    forbidden = {"dlopen", "android_dlopen_ext", "dlsym", "socket", "connect", "getaddrinfo"}
    actual = {line.split()[-1].split("@", 1)[0] for line in imports.splitlines() if line.strip()}
    if unexpected := actual & forbidden:
        raise ValueError(f"dynamic loading or network imports: {sorted(unexpected)}")


def verify_ndk_notice(evidence):
    notice = (evidence / "ndk-NOTICE.toolchain").read_bytes()
    for name in ("ndk-libcxx-NOTICE.txt", "ndk-libcxxabi-NOTICE.txt"):
        canonical = (NATIVE / "licenses/framework" / name).read_bytes()
        if notice.count(canonical) != 1:
            raise ValueError(f"selected NDK notice lacks existing canonical grant: {name}")
    if notice.count(b"The compiler_rt library is dual licensed") != 1:
        raise ValueError("selected NDK notice lacks compiler_rt dual-license heading")


def pinned_ndk_archive(path, abi, ndk):
    if ndk.name != "27.1.12297006" or not path.is_relative_to(ndk):
        return False
    common = ("toolchains", "llvm", "prebuilt", "linux-x86_64", "lib", "clang", "18",
              "lib", "linux")
    arch = {"arm64-v8a": "aarch64", "x86_64": "x86_64"}.get(abi)
    if arch is None:
        return False
    relative = path.relative_to(ndk).parts
    return relative in (
        common + (arch, "libunwind.a"),
        common + ("libclang_rt.builtins-aarch64-android.a",) if abi == "arm64-v8a" else (),
    )


def allowed_upstream_path(path):
    if path.parts[:2] in (("ggml", "include"), ("src", "aneforge")):
        return True
    if path.parts[:3] == ("ggml", "src", "ggml-cpu"):
        return True
    if path.parts[:2] == ("ggml", "src") and len(path.parts) == 3:
        return path.name.startswith("ggml") or path.name == "gguf.cpp"
    return path.as_posix() in {"include/whisper.h", "src/whisper.cpp", "src/whisper-arch.h"}


def compiler_inventory(abi, upstream, entries):
    result = []
    arm = abi == "arm64-v8a"
    blocked = ("__AVX", "__AMX", "__BMI2__", "__F16C__", "__FMA__", "__SSE4_2__",
               "__ARM_FEATURE_SVE", "__ARM_FEATURE_DOTPROD", "__ARM_FEATURE_MATMUL",
               "__ARM_FEATURE_FP16", "_OPENMP", "GGML_BACKEND_DL", "GGML_USE_CPU_REPACK",
               "GGML_USE_OPENMP", "GGML_USE_KLEIDIAI", "WHISPER_USE_OPENVINO",
               "WHISPER_USE_COREML", "WHISPER_USE_VITISAI")
    for entry in entries:
        directory = Path(entry["directory"])
        words = shlex.split(entry["command"])
        output_index = words.index("-o")
        if not (directory / words[output_index + 1]).is_file():
            continue  # EXCLUDE_FROM_ALL targets can appear even when Ninja did not build them.
        source = (directory / entry["file"]).resolve()
        local = VENDOR / "reply_asr.cpp"
        if source.is_relative_to(upstream):
            name = source.relative_to(upstream)
            if not allowed_upstream_path(name):
                raise ValueError(f"unapproved compiled source: {name}")
            name = name.as_posix()
        elif source == local:
            name = "reply_asr.cpp"
        else:
            raise ValueError(f"unexpected source outside verified roots: {source}")
        expected_arch = "-march=armv8-a" if arm else "-march=x86-64"
        if set(flag for flag in words if flag.startswith("-march=")) != {expected_arch} or "-fPIC" not in words:
            raise ValueError(f"compiler architecture or PIC flags differ: {name}")
        # Ask the actual compiler driver with this translation unit's complete
        # target, defines and flags, excluding only input and output operands.
        compile_index = words.index("-c")
        language = "c" if source.suffix == ".c" else "c++"
        flags = [word for index, word in enumerate(words)
                 if index not in (output_index, output_index + 1, compile_index, compile_index + 1)]
        macros_text = subprocess.check_output(
            flags + ["-x", language, "-dM", "-E", "/dev/null"], cwd=directory, text=True)
        macros = set(re.findall(r"^#define (\w+)", macros_text, re.MULTILINE))
        unexpected = sorted(m for m in macros if m.startswith(blocked) or
                            (m.startswith('GGML_USE_') and m != 'GGML_USE_CPU'))
        required = {"__ANDROID__", "__aarch64__" if arm else "__x86_64__"}
        if unexpected or not required <= macros:
            raise ValueError(f"unexpected compiler feature macros for {name}: {unexpected}")
        result.append({"source": name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                       "compiler_macros": sorted(macros)})
    names = {unit["source"] for unit in result}
    expected = {"reply_asr.cpp", "src/whisper.cpp", "src/aneforge/whisper-aneforge.cpp",
                "ggml/src/ggml-cpu/ggml-cpu.c", "ggml/src/ggml.c"}
    if not expected <= names:
        raise ValueError(f"missing real compiler outputs: {sorted(expected - names)}")
    return result


def audit(abi, build, stage, evidence):
    prefix = evidence / abi

    def report(name):
        return prefix.with_name(f"{abi}-{name}.txt").read_text(encoding="utf-8")

    # Reuse the project's tested architecture, DYN and LOAD-alignment parser.
    spec = importlib.util.spec_from_file_location("elf_loads", NATIVE / "scripts/check-pocket-elf.py")
    elf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(elf)
    load_count = elf.check_elf_header_loads(abi, report("header"), report("segments"))
    check_linkage(report("dynamic"), report("symbols"))
    check_imports(report("imports"))
    verify_ndk_notice(evidence)
    verify_notices(stage / "notices")
    upstream = (stage / "whisper.cpp").resolve()

    build_inventory = compiler_inventory(
        abi, upstream, json.loads((build / "compile_commands.json").read_text(encoding="utf-8")))
    included = set()
    for line in report("includes").splitlines():
        if not line.startswith("    "):
            continue
        path = (build / line.strip()).resolve()
        if path.is_relative_to(upstream):
            relative = path.relative_to(upstream)
            if not allowed_upstream_path(relative):
                raise ValueError(f"unapproved upstream include: {relative}")
            included.add(relative.as_posix())
    if not {"include/whisper.h", "ggml/include/ggml.h", "ggml/src/ggml-cpu/vec.h"} <= included:
        raise ValueError("expected upstream include evidence missing")

    archives = set(re.findall(r"([^\s():]+\.a)\(", report("link-map")))
    expected = {"libwhisper.a", "libggml.a", "libggml-base.a", "libggml-cpu.a"}
    verified_runtime = set()
    verified_ndk_runtime = set()
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not sdk:
        raise ValueError("Android SDK root missing for pinned NDK audit")
    ndk = (Path(sdk) / "ndk/27.1.12297006").resolve()
    for archive in archives:
        path = (build / archive).resolve()
        if path.is_relative_to(build / "reply-asr/upstream") and path.name in expected:
            verified_runtime.add(path.name)
        elif pinned_ndk_archive(path, abi, ndk):
            verified_ndk_runtime.add(path.relative_to(ndk).as_posix())
        else:
            raise ValueError(f"unapproved linked archive: {archive}")
    if verified_runtime != expected:
        raise ValueError(f"static library link-map evidence missing: {sorted(expected - verified_runtime)}")
    summary = {"abi": abi, "load_segments_16k": load_count, "static_runtime": sorted(verified_runtime),
               "pinned_ndk_runtime": sorted(verified_ndk_runtime),
               "compiled_sources": build_inventory, "upstream_includes": sorted(included),
               "ndk_notice_sha256": hashlib.sha256((evidence / "ndk-NOTICE.toolchain").read_bytes()).hexdigest(),
               "verified_notices": sorted(path.name for path in (stage / "notices").iterdir())}
    (evidence / f"{abi}-audit.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare").add_argument("output", type=Path)
    smoke = commands.add_parser("smoke")
    for arg in ("binary", "vad_binary", "stage", "evidence"):
        smoke.add_argument(arg, type=Path)
    audit_command = commands.add_parser("audit")
    audit_command.add_argument("abi", choices=("arm64-v8a", "x86_64"))
    for arg in ("build", "stage", "evidence"):
        audit_command.add_argument(arg, type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.output)
    elif args.command == "smoke":
        run_smoke(args.binary, args.vad_binary, args.stage, args.evidence)
    else:
        audit(args.abi, args.build, args.stage, args.evidence)


if __name__ == "__main__":
    main()
