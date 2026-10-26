#!/usr/bin/env python3
"""Bounded local CPU probe of Kyutai's official Pocket TTS Rust binary."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tarfile
import time

try:
    import resource
except ImportError:
    resource = None

REFERENCE = Path(__file__).with_name("pocket-tts-audition.py")
spec = importlib.util.spec_from_file_location("pocket_reference", REFERENCE)
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)

RUST_COMMIT = "a922b912dfa366975294afb568d815b305acde5d"
PTTS_FORK_COMMIT = "7efa26973f81bba42cf2ace8da2c1eb9b605d54d"
TIMEOUT_SECONDS = 60
MAX_OUTPUT_BYTES = 44 + reference.SAMPLE_RATE * reference.MAX_SECONDS * 4 + 4096
DIAGNOSTIC_SOURCE = Path(__file__).resolve().parents[1] / "vendor/pocket-tts-native-probe/sign_token_probe.rs"
TOKEN_PARITY_TEXTS = tuple(text for _, text in reference.UTTERANCES) + (
    "Wait... really?!",
    "Café — naïve résumé ☺",
    "Room 204 costs $12.50 on 10/26/2026.",
)


def digest(path: Path) -> str:
    return reference._digest(path)


def render_local_config(upstream: str, weights: Path, tokenizer: Path) -> str:
    """Select ungated weights directly: the Rust local resolver lacks Python's fallback."""
    replacements = {
        "weights_path": weights.as_posix(),
        "weights_path_without_voice_cloning": "null",
        "    tokenizer_path": tokenizer.as_posix(),
    }
    result = upstream
    for key, value in replacements.items():
        result, count = re.subn(rf"(?m)^{re.escape(key)}: hf://[^\n]+$", f"{key}: {value}", result)
        if count != 1:
            raise ValueError(f"Unexpected upstream config field: {key}")
    if re.search(r"(?:hf|https?)://", result, re.I):
        raise ValueError("Unreviewed remote reference remains in local config")
    if not all(line in result for line in ("sample_rate: 24000", "channels: 1", "n_bins: 4000")):
        raise ValueError("Unexpected audio or tokenizer configuration")
    return result


def local_config(asset_dir: Path, files: dict[str, Path]) -> Path:
    content = render_local_config(
        files[reference.CONFIG_NAME].read_text(encoding="utf-8"),
        files["languages/english_18m_2026-10/model.safetensors"],
        files["languages/english_18m_2026-10/tokenizer.model"],
    )
    scratch = asset_dir / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    dest = scratch / "english_18m_2026-10.rust-local.yaml"
    dest.write_text(content, encoding="utf-8")
    return dest


def inspect_wav(path: Path) -> tuple[bytes, dict]:
    """Decode only mono 24 kHz PCM16 or IEEE float32 RIFF, then reject bad audio."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_OUTPUT_BYTES:
        raise ValueError("Missing, linked or oversized WAV")
    data = path.read_bytes()
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE" or struct.unpack_from("<I", data, 4)[0] != len(data) - 8:
        raise ValueError("Malformed RIFF WAV")
    cursor, fmt, frames = 12, None, None
    while cursor + 8 <= len(data):
        tag, size = data[cursor:cursor + 4], struct.unpack_from("<I", data, cursor + 4)[0]
        cursor += 8
        if cursor + size > len(data):
            raise ValueError("Truncated WAV chunk")
        chunk = data[cursor:cursor + size]
        if tag == b"fmt ":
            if fmt is not None or size < 16:
                raise ValueError("Invalid WAV format chunk")
            fmt = struct.unpack_from("<HHIIHH", chunk)
        elif tag == b"data":
            if frames is not None:
                raise ValueError("Multiple WAV data chunks")
            frames = chunk
        cursor += size + size % 2
    if fmt is None or frames is None or cursor != len(data):
        raise ValueError("Missing WAV format or data")
    kind, channels, rate, byte_rate, block_align, bits = fmt
    if channels != 1 or rate != reference.SAMPLE_RATE or (kind, bits) not in ((1, 16), (3, 32)):
        raise ValueError("WAV must be mono 24 kHz PCM16 or float32")
    width = bits // 8
    if byte_rate != rate * width or block_align != width or len(frames) % width:
        raise ValueError("Inconsistent WAV format")
    count = len(frames) // width
    if count == 0 or count > rate * reference.MAX_SECONDS:
        raise ValueError("WAV duration outside probe bound")
    values = struct.iter_unpack("<h" if kind == 1 else "<f", frames)
    peak = 0.0
    squares = 0.0
    pcm = bytearray()
    for (sample,) in values:
        sample = sample / 32767.0 if kind == 1 else sample
        if not math.isfinite(sample):
            raise ValueError("Nonfinite WAV sample")
        peak = max(peak, abs(sample))
        squares += sample * sample
        if kind == 3 and abs(sample) < 1:
            pcm.extend(struct.pack("<h", round(sample * 32767)))
    if peak < reference.MIN_PEAK or peak >= 1.0:
        raise ValueError("Silent or clipped WAV")
    if kind == 1:
        pcm = frames
    return bytes(pcm), {"samples": count, "duration_seconds": round(count / rate, 4), "peak": round(peak, 6), "rms": round(math.sqrt(squares / count), 6), "source_format": "pcm16" if kind == 1 else "float32"}


def run_generation(binary: Path, config: Path, voice: Path, output_dir: Path, utterances=reference.UTTERANCES, timeout: int = TIMEOUT_SECONDS) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    env = os.environ.copy()
    env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": "", "RAYON_NUM_THREADS": "2", "POCKET_TTS_SEED": "42"})
    for stem, text in utterances:
        path = output_dir / f"{stem}.wav"
        path.unlink(missing_ok=True)
        cmd = [str(binary), "generate", "--config", str(config), "--voice", str(voice), "--text", text,
               "--output-path", str(path), "--device", "cpu", "--max-tokens", str(reference.MAX_TOKENS)]
        start = time.monotonic()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, check=False)
        except subprocess.TimeoutExpired as exc:
            path.unlink(missing_ok=True)
            raise RuntimeError(f"Rust generation timed out after {timeout}s for {stem}") from exc
        except KeyboardInterrupt:
            path.unlink(missing_ok=True)
            raise
        seconds = round(time.monotonic() - start, 3)
        if proc.returncode:
            path.unlink(missing_ok=True)
            raise RuntimeError(f"Rust generation failed for {stem}: {proc.stderr[-2000:]}")
        try:
            pcm, stats = inspect_wav(path)
        except ValueError:
            path.unlink(missing_ok=True)
            raise
        if stats["source_format"] == "float32":
            import wave
            with wave.open(str(path), "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(reference.SAMPLE_RATE)
                writer.writeframes(pcm)
        outputs.append({"text": text, "file": path.name, "sha256": digest(path),
                        "cli_startup_model_load_and_generation_seconds": seconds, **stats})
    return outputs


def compare_token_ids(model: Path, diagnostic: Path) -> dict:
    """Compare actual official Rust tokenizer IDs with Python SentencePiece IDs."""
    if not diagnostic.is_file() or diagnostic.is_symlink():
        raise ValueError("Missing built Rust tokenizer diagnostic")
    if not model.is_file() or model.is_symlink():
        raise ValueError("Missing verified tokenizer model")
    import sentencepiece as spm
    python_tokenizer = spm.SentencePieceProcessor(model_file=str(model))
    python_ids = [python_tokenizer.encode(text, out_type=int) for text in TOKEN_PARITY_TEXTS]
    proc = subprocess.run([str(diagnostic), str(model)], input=json.dumps(TOKEN_PARITY_TEXTS),
                          capture_output=True, text=True, timeout=30, check=False,
                          env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    if proc.returncode or not proc.stdout.strip():
        raise ValueError(f"Missing or failed Rust tokenizer diagnostic output: {proc.stderr[-1000:]}")
    try:
        rust_ids = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid Rust tokenizer diagnostic output") from exc
    if not isinstance(rust_ids, list) or len(rust_ids) != len(TOKEN_PARITY_TEXTS) or any(
        not isinstance(row, list) or not row or any(type(value) is not int or value < 0 for value in row)
        for row in rust_ids
    ):
        raise ValueError("Invalid Rust tokenizer diagnostic output shape")
    if rust_ids != python_ids:
        mismatch = next(i for i, (rust, python) in enumerate(zip(rust_ids, python_ids)) if rust != python)
        raise ValueError(f"Token ID mismatch for case {mismatch + 1}: Rust {rust_ids[mismatch]}, Python {python_ids[mismatch]}")
    return {"status": "exact_raw_sentencepiece_ids_match", "scope": "Raw SentencePiece IDs only; prepared text and model math unverified",
            "diagnostic_sha256": digest(diagnostic), "diagnostic_source_sha256": digest(DIAGNOSTIC_SOURCE),
            "cases": [{"text": text, "ids": ids} for text, ids in zip(TOKEN_PARITY_TEXTS, rust_ids)]}


def rust_source_identity(source: Path, archive: Path, manifest: dict) -> dict:
    if not source.is_dir() or source.is_symlink():
        raise ValueError("Rust extracted source missing or linked")
    reference._source_checkout(source, archive, manifest)
    prefix = f"pocket-tts-{RUST_COMMIT}/"
    expected = set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if member.name == prefix.rstrip("/") and member.isdir():
                continue
            if not member.name.startswith(prefix):
                raise ValueError("Unexpected source archive prefix")
            relative = member.name[len(prefix):]
            if not relative.startswith("rust/") or not member.isfile():
                continue
            if not (relative.endswith(".rs") or relative in ("rust/Cargo.toml", "rust/Cargo.lock", "rust/.cargo/config.toml")):
                continue
            path = source / relative
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source.resolve()):
                raise ValueError(f"Missing or linked Rust source: {relative}")
            stream = tar.extractfile(member)
            if stream is None or hashlib.sha256(stream.read()).hexdigest() != digest(path):
                raise ValueError(f"Extracted Rust source differs from pinned archive: {relative}")
            expected.add(relative)
    actual = {p.relative_to(source).as_posix() for p in (source / "rust").rglob("*.rs") if "target" not in p.parts}
    diagnostic_relative = "rust/src/bin/sign_token_probe.rs"
    diagnostic = source / diagnostic_relative
    if diagnostic_relative in actual:
        if diagnostic.is_symlink() or digest(diagnostic) != digest(DIAGNOSTIC_SOURCE):
            raise ValueError("Rust diagnostic differs from reviewed repository source")
        actual.remove(diagnostic_relative)
    if not actual.issubset(expected):
        raise ValueError("Unverified extra Rust source file")
    cargo = source / "rust" / "Cargo.toml"
    lock = source / "rust" / "Cargo.lock"
    if not cargo.is_file() or not lock.is_file():
        raise ValueError("Official Rust Cargo manifest or lock missing")
    contents = cargo.read_text(encoding="utf-8")
    if PTTS_FORK_COMMIT not in contents:
        raise ValueError("Unexpected ptts fork revision")
    if PTTS_FORK_COMMIT not in lock.read_text(encoding="utf-8"):
        raise ValueError("Cargo.lock does not pin the expected ptts fork")
    return {"source_commit": RUST_COMMIT, "source_archive_sha256": manifest["source"]["archive_sha256"], "rust_cargo_toml_sha256": digest(cargo), "rust_cargo_lock_sha256": digest(lock), "ptts_fork_commit": PTTS_FORK_COMMIT}


def version(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"Cannot identify runtime: {command[0]}") from exc
    return result.stdout.strip()


def audition(asset_dir: Path, source_dir: Path, source_archive: Path, binary: Path, diagnostic: Path, output_dir: Path) -> Path:
    manifest = reference._manifest()
    files = reference.verify_assets(asset_dir, manifest)
    source = rust_source_identity(source_dir, source_archive, manifest)
    if not binary.is_file() or binary.is_symlink() or not binary.resolve().is_relative_to((source_dir / "rust").resolve()):
        raise ValueError("Binary must be the built official Rust checkout binary")
    if not diagnostic.is_file() or diagnostic.is_symlink() or diagnostic.resolve() != (source_dir / "rust/target/release/sign_token_probe").resolve():
        raise ValueError("Diagnostic must be the built reviewed Rust source")
    parity = compare_token_ids(files["languages/english_18m_2026-10/tokenizer.model"], diagnostic)
    config = local_config(asset_dir, files)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "result.json").unlink(missing_ok=True)
    outputs = run_generation(binary, config, files["languages/english_18m_2026-10/embeddings/alba.safetensors"], output_dir)
    try:
        peak_rss_kib = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss if resource else None
    except (AttributeError, ValueError):
        peak_rss_kib = None
    result = {
        "status": "synthesized_for_native_cpu_probe_only",
        **source,
        "binary_sha256": digest(binary),
        "binary_version": version([str(binary), "--version"]),
        "rustc_version": version(["rustc", "--version"]),
        "cargo_version": version(["cargo", "--version"]),
        "source_version": manifest["source"]["version"],
        "model_revision": manifest["model"]["revision"],
        "model_language": manifest["model"]["language"],
        "voice": manifest["model"]["voice"],
        "asset_sha256": {item["path"]: item["sha256"] for item in manifest["model"]["assets"]},
        "upstream_config_sha256": manifest["source"]["config_sha256"],
        "local_config_sha256": digest(config),
        "config_change": "Direct local ungated weights selection; no voice cloning or remote model reference",
        "sample_rate": reference.SAMPLE_RATE,
        "channels": 1,
        "output_format": "signed_16_bit_little_endian_pcm",
        "max_tokens_per_chunk": reference.MAX_TOKENS,
        "per_utterance_timeout_seconds": TIMEOUT_SECONDS,
        "seed": 42,
        "rayon_threads": 2,
        "peak_child_rss_kib": peak_rss_kib,
        "token_id_parity": parity,
        "python_version": sys.version.split()[0],
        "scope": "Official Rust CPU CLI probe; five fixed phrases; no Android backend, app integration, or runtime model provisioning. CLI has unbounded latent channel; future adapter must provide bounded flow and cancellation. Text --max-tokens is not an audio-frame limit.",
        "outputs": outputs,
        "attribution": manifest["attribution"],
    }
    destination = output_dir / "result.json"
    destination.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audition",))
    parser.add_argument("--asset-dir", type=Path, required=True)
    parser.add_argument("--rust-source-dir", type=Path, required=True)
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--diagnostic", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(audition(args.asset_dir, args.rust_source_dir, args.source_archive, args.binary, args.diagnostic, args.output_dir))


if __name__ == "__main__":
    main()
