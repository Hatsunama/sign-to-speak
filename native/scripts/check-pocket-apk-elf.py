#!/usr/bin/env python3
"""Audit final APK Pocket binaries rather than build intermediates."""
from pathlib import Path
import importlib.util
import struct
import subprocess
import sys
import tempfile
import zipfile

spec = importlib.util.spec_from_file_location("pocket_elf", Path(__file__).with_name("check-pocket-elf.py"))
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
check = module.check
check_elf_header_loads = module.check_elf_header_loads
asset_spec = importlib.util.spec_from_file_location("pocket_apk_assets", Path(__file__).with_name("check-phone-qa.py"))
assert asset_spec and asset_spec.loader
asset_module = importlib.util.module_from_spec(asset_spec)
asset_spec.loader.exec_module(asset_module)


def check_stored_payload_alignment(apk: Path, info: zipfile.ZipInfo) -> None:
    if info.compress_type != zipfile.ZIP_STORED:
        return
    with apk.open("rb") as raw:
        raw.seek(info.header_offset)
        local = raw.read(30)
        if len(local) != 30 or local[:4] != b"PK\x03\x04":
            raise ValueError(f"Invalid ZIP local header: {info.filename}")
        filename_size, extra_size = struct.unpack_from("<HH", local, 26)
        name_bytes = raw.read(filename_size)
        encoding = "utf-8" if info.flag_bits & 0x800 else "cp437"
        if name_bytes.decode(encoding) != info.filename:
            raise ValueError(f"ZIP local filename differs: {info.filename}")
        payload_offset = info.header_offset + 30 + filename_size + extra_size
        if payload_offset % 16384:
            raise ValueError(f"Stored native library lacks 16 KiB ZIP payload alignment: {info.filename}")


def main(apk: Path, abi: str, readelf: Path, evidence: Path) -> None:
    if abi not in ("arm64-v8a", "x86_64"):
        raise ValueError(f"Unsupported ABI: {abi}")
    required = {f"lib/{abi}/{name}" for name in
                ("libsign_pocket_tts_runtime.so", "libprivate_pocket_tts.so", "libc++_shared.so")}
    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        asset_module.verify_pocket_assets(archive, names)
        asset_module.verify_application_assets(archive, names)
        if len(names) != len(set(names)) or not required <= set(names):
            raise ValueError(f"Missing or duplicate APK native libraries: {sorted(required - set(names))}")
        if any(asset_module.is_retired_speech_artifact(name) for name in names):
            raise ValueError("Retired Flite artifact found in APK")
        native_prefix = f"lib/{abi}/"
        if any(name.startswith("lib/") and name.endswith(".so") and
               not name.startswith(native_prefix) for name in names):
            raise ValueError(f"Unexpected APK native ABI outside {native_prefix}")
        native = [info for info in archive.infolist() if info.filename.startswith(native_prefix)]
        if any(not info.filename.endswith(".so") or "/" in info.filename[len(native_prefix):] or
               "\\" in info.filename or ":" in info.filename[len(native_prefix):] or
               info.is_dir() for info in native):
            raise ValueError(f"Unexpected native library path under {native_prefix}")
        with tempfile.TemporaryDirectory(prefix="pocket-elf-") as scratch:
            for index, info in enumerate(native):
                check_stored_payload_alignment(apk, info)
                filename = info.filename[len(native_prefix):]
                so = Path(scratch) / str(index) / filename
                so.parent.mkdir()
                so.write_bytes(archive.read(info))
                header = subprocess.check_output([str(readelf), "-hW", str(so)], text=True)
                segments = subprocess.check_output([str(readelf), "-lW", str(so)], text=True)
                try:
                    check_elf_header_loads(abi, header, segments)
                except ValueError as error:
                    raise ValueError(f"{info.filename}: {error}") from error
                role = {"libsign_pocket_tts_runtime.so": "runtime",
                        "libprivate_pocket_tts.so": "jni"}.get(filename)
                if role:
                    prefix = evidence / f"{abi}-{role}"
                    for suffix, data in (("header", header), ("segments", segments)):
                        Path(str(prefix) + f"-{suffix}.txt").write_text(data)
                    for flag, suffix in (("-dW", "dynamic"), ("-Ws", "symbols")):
                        data = subprocess.check_output([str(readelf), flag, str(so)], text=True)
                        Path(str(prefix) + f"-{suffix}.txt").write_text(data)
                    check(abi, prefix, role)
    print(f"Final {abi} APK native ELF and ZIP alignment audit passed ({len(native)} libraries)")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit("usage: check-pocket-apk-elf.py APK ABI NDK_LLVM_READELF EVIDENCE_DIR")
    Path(sys.argv[4]).mkdir(parents=True, exist_ok=True)
    main(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))
