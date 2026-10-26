#!/usr/bin/env python3
"""Fail closed on Pocket Android ELF identity, linkage, and 16 KiB LOAD alignment."""
from pathlib import Path
import re
import sys


def check(abi: str, prefix: Path, role: str) -> None:
    header = Path(str(prefix) + "-header.txt").read_text()
    segments = Path(str(prefix) + "-segments.txt").read_text()
    dynamic = Path(str(prefix) + "-dynamic.txt").read_text()
    symbols = Path(str(prefix) + "-symbols.txt").read_text()
    machine = {"arm64-v8a": "AArch64", "x86_64": "Advanced Micro Devices X86-64"}[abi]
    if not re.search(r"Machine:\s*" + re.escape(machine), header) or not re.search(r"Type:\s*DYN", header):
        raise ValueError(f"Expected {abi} shared object")
    loads = [line.split() for line in segments.splitlines() if line.lstrip().startswith("LOAD ")]
    if not loads:
        raise ValueError("No ELF LOAD segments")
    for fields in loads:
        offset, address, alignment = (int(fields[i], 16) for i in (1, 2, -1))
        if alignment < 16384 or offset % 16384 != address % 16384:
            raise ValueError(f"LOAD segment is not 16 KiB compatible: {fields}")
    needed = re.findall(r"\(NEEDED\).*?\[([^]]+)\]", dynamic)
    rust_name = "libsign_pocket_tts_runtime.so"
    jni_name = "libprivate_pocket_tts.so"
    expected_soname = rust_name if role == "runtime" else jni_name
    soname = re.findall(r"\(SONAME\).*?\[([^]]+)\]", dynamic)
    if soname != [expected_soname] or re.search(r"\((?:RPATH|RUNPATH)\)", dynamic):
        raise ValueError(f"Unexpected SONAME or runtime path: {soname}")
    allowed = {"libc.so", "libm.so", "libdl.so", "liblog.so", "libc++_shared.so"}
    if role == "jni":
        allowed.add(rust_name)
        if rust_name not in needed:
            raise ValueError("JNI bridge does not link the packaged Rust runtime")
    if not needed or any("/" in name or "\\" in name or name not in allowed for name in needed):
        raise ValueError(f"Unexpected DT_NEEDED libraries: {needed}")
    # -Ws contains both .dynsym and .symtab; only default-visible dynamic
    # symbols can satisfy a caller loading this library from the APK.
    exported = set()
    in_dynsym = False
    for line in symbols.splitlines():
        if line.startswith("Symbol table '"):
            in_dynsym = line.startswith("Symbol table '.dynsym'")
            continue
        if not in_dynsym:
            continue
        fields = line.split()
        if (len(fields) >= 8 and fields[0].endswith(':') and fields[3] == "FUNC" and
                fields[4] == "GLOBAL" and fields[5] == "DEFAULT" and fields[6] != "UND"):
            exported.add(fields[7].split("@")[0])
    if role == "runtime":
        required = {"sts_pocket_create", "sts_pocket_synthesize", "sts_pocket_cancel", "sts_pocket_close"}
        if not required <= exported:
            raise ValueError(f"Missing C ABI exports: {sorted(required - exported)}")
    else:
        required = {"Java_com_xmilo_1at_1your_1side_sign_1to_1speak_PocketTtsRequest_native" + name
                    for name in ("Create", "Synthesize", "Cancel", "Close")}
        if required != exported:
            raise ValueError(f"JNI exports differ: missing={sorted(required - exported)}, extra={sorted(exported - required)}")
    print(f"{abi} {role}: {len(loads)} 16 KiB LOAD segments, SONAME and dependencies verified")


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("arm64-v8a", "x86_64") or sys.argv[3] not in ("runtime", "jni"):
        raise SystemExit("usage: check-pocket-elf.py arm64-v8a|x86_64 PREFIX runtime|jni")
    check(sys.argv[1], Path(sys.argv[2]), sys.argv[3])
