#!/usr/bin/env python3
"""Audit final APK Pocket binaries rather than build intermediates."""
from pathlib import Path
import importlib.util
import subprocess
import sys
import tempfile
import zipfile

spec = importlib.util.spec_from_file_location("pocket_elf", Path(__file__).with_name("check-pocket-elf.py"))
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
check = module.check
asset_spec = importlib.util.spec_from_file_location("pocket_apk_assets", Path(__file__).with_name("check-phone-qa.py"))
assert asset_spec and asset_spec.loader
asset_module = importlib.util.module_from_spec(asset_spec)
asset_spec.loader.exec_module(asset_module)


def main(apk: Path, abi: str, readelf: Path, evidence: Path) -> None:
    required = {f"lib/{abi}/{name}" for name in
                ("libsign_pocket_tts_runtime.so", "libprivate_pocket_tts.so", "libc++_shared.so")}
    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        asset_module.verify_pocket_assets(archive, names)
        if len(names) != len(set(names)) or not required <= set(names):
            raise ValueError(f"Missing or duplicate APK native libraries: {sorted(required - set(names))}")
        if any(asset_module.is_retired_speech_artifact(name) for name in names):
            raise ValueError("Retired Flite artifact found in APK")
        with tempfile.TemporaryDirectory(prefix="pocket-elf-") as scratch:
            for role, filename in (("runtime", "libsign_pocket_tts_runtime.so"),
                                   ("jni", "libprivate_pocket_tts.so")):
                so = Path(scratch) / filename
                so.write_bytes(archive.read(f"lib/{abi}/{filename}"))
                prefix = evidence / f"{abi}-{role}"
                for flag, suffix in (("-hW", "header"), ("-lW", "segments"),
                                     ("-dW", "dynamic"), ("-Ws", "symbols")):
                    data = subprocess.check_output([str(readelf), flag, str(so)], text=True)
                    Path(str(prefix) + f"-{suffix}.txt").write_text(data)
                check(abi, prefix, role)
            cxx = Path(scratch) / "libc++_shared.so"
            cxx.write_bytes(archive.read(f"lib/{abi}/libc++_shared.so"))
            segments = subprocess.check_output([str(readelf), "-lW", str(cxx)], text=True)
            loads = [line.split() for line in segments.splitlines() if line.lstrip().startswith("LOAD ")]
            if not loads or any(int(fields[-1], 16) < 16384 or
                                int(fields[1], 16) % 16384 != int(fields[2], 16) % 16384
                                for fields in loads):
                raise ValueError("APK libc++_shared.so lacks 16 KiB LOAD alignment")
    print(f"Final {abi} APK Pocket/JNI/STL ELF audit passed")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit("usage: check-pocket-apk-elf.py APK ABI NDK_LLVM_READELF EVIDENCE_DIR")
    Path(sys.argv[4]).mkdir(parents=True, exist_ok=True)
    main(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))
