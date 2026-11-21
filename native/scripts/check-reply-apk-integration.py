#!/usr/bin/env python3
"""Require the reviewed local-reply ASR packaging contract in a final Android APK.

This is intentionally a fail-before packaging gate. It inspects packaged bytes,
the APK's decoded manifest, DEX class definitions, and final ELF files. It does
not claim microphone runtime, transcription quality, or device execution.
"""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ANDROID = "{http://schemas.android.com/apk/res/android}"
MODEL_ASSETS = {
    "assets/reply-asr/ggml-tiny.en.bin": (
        77704715,
        "921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f",
    ),
    "assets/reply-asr/ggml-silero-v6.2.0.bin": (
        885098,
        "2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987",
    ),
}
NOTICE_ASSETS = {
    "assets/reply-asr-notices/ARM-EXP-COPYRIGHT.txt": (158, "bbba1005b7a0ecee1fb059e81a755d0fd2f9b9fa78371fa39d8d378963d92571"),
    "assets/reply-asr-notices/ARM-OPTIMIZED-ROUTINES-LICENSE.txt": (13491, "650afbf29f214451e02241adc42534e82c9d6ae2b38e2444b92b5a1ffcaf9346"),
    "assets/reply-asr-notices/ATTRIBUTION.txt": (1269, "63299e2ee1c91e6cd3814897ec8c1eae8f67b28f5e46af216d2c611740971e89"),
    "assets/reply-asr-notices/FP16-MIT.txt": (1157, "17e4f539024be2749ee729d1e2f01d24cef12ece8c9bf18e91a4349be29c80bf"),
    "assets/reply-asr-notices/OPENAI-GPT2-LICENSE.txt": (1403, "0dbeda4bc78823b1d67ec0ee414d8690ee4cad826bc9c147b215adc3af202436"),
    "assets/reply-asr-notices/OPENAI-WHISPER-MIT.txt": (1063, "b5d65a59060e68c4ff940e1eddfa6f94b2d68fdf58ed7f4dd57721c997e35e9d"),
    "assets/reply-asr-notices/SILERO-MIT.txt": (1075, "2e63e9a38b6e8fc0c7bc37ce174caca1862870856c6daf5697cfb785e925520b"),
    "assets/reply-asr-notices/WHISPER-CPP-MIT.txt": (1078, "94f29bbed6a22c35b992c5c6ebf0e7c92f13b836b90f36f461c9cf2f0f1d010d"),
    "assets/reply-asr-notices/YARN-MIT.txt": (1088, "c3ec00ded224ebaaa673373961babc2d79f3081914897f3325cb7927a76234c1"),
}
MODULE_CLASS = b"Lcom/xmilo_at_your_side/sign_to_speak/LocalReplyModule;"
REQUEST_CLASS = b"Lcom/xmilo_at_your_side/sign_to_speak/ReplyAsrRequest;"
RUNTIME_EXPORTS = {"sts_reply_open", "sts_reply_decode", "sts_reply_close"}
JNI_EXPORTS = {
    "Java_com_xmilo_1at_1your_1side_sign_1to_1speak_ReplyAsrRequest_nativeOpen",
    "Java_com_xmilo_1at_1your_1side_sign_1to_1speak_ReplyAsrRequest_nativeDecode",
    "Java_com_xmilo_1at_1your_1side_sign_1to_1speak_ReplyAsrRequest_nativeCancel",
    "Java_com_xmilo_1at_1your_1side_sign_1to_1speak_ReplyAsrRequest_nativeClose",
}

elf_spec = importlib.util.spec_from_file_location(
    "pocket_elf", Path(__file__).with_name("check-pocket-elf.py"))
assert elf_spec and elf_spec.loader
elf_module = importlib.util.module_from_spec(elf_spec)
elf_spec.loader.exec_module(elf_module)
check_elf_header_loads = elf_module.check_elf_header_loads


def dex_classes(blob):
    """Read class_def descriptors, not incidental DEX string references."""
    if len(blob) < 112 or not re.fullmatch(rb"dex\n0[0-9]{2}\x00", blob[:8]):
        raise ValueError("Invalid DEX header")
    size, header, endian = struct.unpack_from("<III", blob, 32)
    if size != len(blob) or header != 112 or endian != 0x12345678:
        raise ValueError("Invalid DEX size or byte order")
    string_count, string_offset, type_count, type_offset = struct.unpack_from("<IIII", blob, 56)
    class_count, class_offset = struct.unpack_from("<II", blob, 96)
    if (string_count > len(blob) // 4 or string_offset + string_count * 4 > len(blob) or
            type_count > len(blob) // 4 or type_offset + type_count * 4 > len(blob) or
            class_count > len(blob) // 32 or class_offset + class_count * 32 > len(blob)):
        raise ValueError("DEX table outside file")
    result = set()
    for index in range(class_count):
        class_index, = struct.unpack_from("<I", blob, class_offset + index * 32)
        if class_index >= type_count:
            raise ValueError("DEX class type outside table")
        string_index, = struct.unpack_from("<I", blob, type_offset + class_index * 4)
        if string_index >= string_count:
            raise ValueError("DEX descriptor outside string table")
        offset, = struct.unpack_from("<I", blob, string_offset + string_index * 4)
        for _ in range(5):
            if offset >= len(blob):
                raise ValueError("DEX string outside file")
            byte = blob[offset]
            offset += 1
            if byte < 0x80:
                break
        else:
            raise ValueError("Invalid DEX string length")
        end = blob.find(b"\0", offset, min(offset + 4096, len(blob)))
        if end < 0:
            raise ValueError("DEX descriptor too long or unterminated")
        result.add(blob[offset:end])
    return result


def verify_manifest(manifest):
    root = ET.parse(manifest).getroot()
    package = root.get("package")
    if package not in ("com.xmilo_at_your_side.sign_to_speak",
                        "com.xmilo_at_your_side.sign_to_speak.qa"):
        raise ValueError("Unexpected application package")
    permissions = [node.get(ANDROID + "name") for node in root if node.tag in
                   ("uses-permission", "uses-permission-sdk-23", "uses-permission-sdk-m")]
    expected = {"android.permission.CAMERA", "android.permission.RECORD_AUDIO",
                package + ".DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"}
    if len(permissions) != 3 or set(permissions) != expected:
        raise ValueError(f"Reply microphone/no-network permission contract differs: {permissions}")
    if "android.permission.INTERNET" in permissions:
        raise ValueError("Reply packaging contract must remain network-free")
    application = root.find("application")
    if (application is None or application.get(ANDROID + "allowAudioPlaybackCapture") != "false" or
            application.get(ANDROID + "allowBackup") != "false" or
            application.get(ANDROID + "usesCleartextTraffic") != "false"):
        raise ValueError("Application privacy attributes changed")


def check_asset(archive, name, size, digest):
    info = archive.getinfo(name)
    if (info.file_size != size or info.compress_size != size or
            info.compress_type != zipfile.ZIP_STORED or info.flag_bits & 1):
        raise ValueError(f"Reply model is not exact, uncompressed, descriptor-accessible: {name}")
    actual = hashlib.sha256()
    with archive.open(info) as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            actual.update(block)
    if actual.hexdigest() != digest:
        raise ValueError(f"Reply model SHA-256 mismatch: {name}")


def check_notice(archive, name, size, digest):
    info = archive.getinfo(name)
    if info.file_size != size or info.flag_bits & 1:
        raise ValueError(f"Reply notice is not exact or is encrypted: {name}")
    actual = hashlib.sha256()
    with archive.open(info) as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            actual.update(block)
    if actual.hexdigest() != digest:
        raise ValueError(f"Reply notice SHA-256 mismatch: {name}")


def dynamic_exports(symbols):
    result = set()
    dynamic = False
    for line in symbols.splitlines():
        if line.startswith("Symbol table '"):
            dynamic = line.startswith("Symbol table '.dynsym'")
            continue
        fields = line.split()
        if (dynamic and len(fields) >= 8 and fields[0].endswith(":") and
                fields[5] == "DEFAULT" and fields[6] != "UND"):
            result.add(fields[7].split("@", 1)[0])
    return result


def verify_elf(path, abi, role, readelf, evidence):
    reports = {}
    for flag, label in (("-hW", "header"), ("-lW", "segments"),
                        ("-dW", "dynamic"), ("-Ws", "symbols")):
        reports[label] = subprocess.check_output([str(readelf), flag, str(path)], text=True)
        (evidence / f"{abi}-reply-{role}-{label}.txt").write_text(reports[label])
    try:
        check_elf_header_loads(abi, reports["header"], reports["segments"])
    except ValueError as error:
        raise ValueError(f"Reply {role} ELF differs: {error}") from error
    expected_name = "libsign_reply_asr.so" if role == "runtime" else "libprivate_reply_asr.so"
    soname = re.findall(r"\(SONAME\).*?\[([^]]+)\]", reports["dynamic"])
    needed = re.findall(r"\(NEEDED\).*?\[([^]]+)\]", reports["dynamic"])
    allowed = {"libc.so", "libm.so", "libdl.so", "liblog.so", "libc++_shared.so"}
    if role == "jni":
        allowed.add("libsign_reply_asr.so")
    if (soname != [expected_name] or re.search(r"\((?:RPATH|RUNPATH)\)", reports["dynamic"]) or
            not needed or set(needed) - allowed or
            (role == "jni" and "libsign_reply_asr.so" not in needed)):
        raise ValueError(f"Reply {role} ELF linkage differs: SONAME={soname}, NEEDED={needed}")
    expected = RUNTIME_EXPORTS if role == "runtime" else JNI_EXPORTS
    actual = dynamic_exports(reports["symbols"])
    if actual != expected:
        raise ValueError(f"Reply {role} ELF exports differ: {sorted(actual)}")


def verify(manifest, apk, abi, readelf, evidence):
    if abi not in ("arm64-v8a", "x86_64"):
        raise ValueError("Unreviewed app ABI")
    verify_manifest(manifest)
    evidence.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip() is not None:
            raise ValueError("Duplicate or damaged APK ZIP entry")
        actual_models = {name for name in names
                         if name.startswith("assets/reply-asr/") and not name.endswith("/")}
        if actual_models != set(MODEL_ASSETS):
            raise ValueError(f"Reply model APK inventory differs: {sorted(actual_models)}")
        for name, (size, digest) in MODEL_ASSETS.items():
            check_asset(archive, name, size, digest)
        actual_notices = {name for name in names
                          if name.startswith("assets/reply-asr-notices/") and not name.endswith("/")}
        if actual_notices != set(NOTICE_ASSETS):
            raise ValueError(f"Reply notice APK inventory differs: {sorted(actual_notices)}")
        for name, (size, digest) in NOTICE_ASSETS.items():
            check_notice(archive, name, size, digest)
        libraries = {"runtime": f"lib/{abi}/libsign_reply_asr.so",
                     "jni": f"lib/{abi}/libprivate_reply_asr.so"}
        if any(name.endswith(("/libsign_reply_asr.so", "/libprivate_reply_asr.so")) and
               name not in libraries.values() for name in names):
            raise ValueError("Reply library packaged for an unexpected ABI or path")
        missing = set(libraries.values()) - set(names)
        if missing:
            raise ValueError(f"Reply APK libraries absent: {sorted(missing)}")
        dex_names = [name for name in names if re.fullmatch(r"classes(?:[2-9]|[1-9][0-9]+)?\.dex", name)]
        if not dex_names or any(name.lower().endswith(".dex") and name not in dex_names for name in names):
            raise ValueError("App DEX inventory is absent or nonstandard")
        defined = set().union(*(dex_classes(archive.read(name)) for name in dex_names))
        if MODULE_CLASS not in defined:
            raise ValueError("Local reply microphone bridge class absent from app DEX")
        if REQUEST_CLASS not in defined:
            raise ValueError("Reply ASR request class absent from app DEX")
        with tempfile.TemporaryDirectory(prefix="reply-apk-") as scratch:
            for role, name in libraries.items():
                target = Path(scratch) / Path(name).name
                target.write_bytes(archive.read(name))
                verify_elf(target, abi, role, readelf, evidence)
    print(f"Actual {abi} APK satisfies the pinned local reply packaging contract")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("apk", type=Path)
    parser.add_argument("--abi", required=True, choices=("arm64-v8a", "x86_64"))
    parser.add_argument("--readelf", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    verify(args.manifest, args.apk, args.abi, args.readelf, args.evidence)
