#!/usr/bin/env python3
"""Bounded, CPU-only Pocket TTS listening audition. No app integration."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import urllib.request
import wave

MANIFEST = Path(__file__).resolve().parents[1] / "vendor/pocket-tts-audition/manifest.json"
CONFIG_NAME = "english_18m_2026-10.upstream.yaml"
LOCAL_CONFIG = "english_18m_2026-10.local.yaml"
UTTERANCES = (
    ("01-introduction", "Hello, I can speak for you."),
    ("02-request", "Please call my family."),
    ("03-need", "I need water, please."),
    ("04-number", "The appointment is at 3:45 p.m."),
    ("05-punctuation", "Dr. Smith said it’s okay — thank you."),
)
SAMPLE_RATE = 24000
MAX_TOKENS = 160
MAX_SECONDS = 16
MIN_PEAK = 0.001


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8-sig"))


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _check_file(path: Path, size: int, digest: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Missing or linked pinned asset: {path}")
    if path.stat().st_size != size:
        raise ValueError(f"Pinned asset size mismatch: {path}")
    if _digest(path) != digest:
        raise ValueError(f"Pinned asset SHA-256 mismatch: {path}")


def verify_assets(asset_dir: Path, manifest: dict) -> dict[str, Path]:
    """Verify every admitted byte before model import or inference."""
    root = asset_dir.resolve(strict=True)
    source = manifest["source"]
    model = manifest["model"]
    if source["commit"] != "a922b912dfa366975294afb568d815b305acde5d" or source["version"] != "4.0.1":
        raise ValueError("Unapproved source revision")
    if model["revision"] != "a7056f505a4a8ca72d8b5fb1440071d6f1a35739" or model["language"] != "english_18m_2026-10" or model["voice"] != "alba":
        raise ValueError("Unapproved model or voice")
    expected = {
        "languages/english_18m_2026-10/model.safetensors",
        "languages/english_18m_2026-10/tokenizer.model",
        "languages/english_18m_2026-10/embeddings/alba.safetensors",
    }
    if {item["path"] for item in model["assets"]} != expected or len(model["assets"]) != 3:
        raise ValueError("Asset selection differs from approved three files")
    files = {}
    for item in model["assets"]:
        path = root / item["path"]
        if not path.resolve().is_relative_to(root):
            raise ValueError("Asset path escapes audition root")
        _check_file(path, item["size"], item["sha256"])
        files[item["path"]] = path
    config = root / CONFIG_NAME
    _check_file(config, source["config_size"], source["config_sha256"])
    files[CONFIG_NAME] = config
    return files


def _download(url: str, destination: Path, size: int, digest: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        _check_file(destination, size, digest)
        return
    partial = destination.with_suffix(destination.suffix + ".part")
    partial.unlink(missing_ok=True)
    h = hashlib.sha256()
    count = 0
    request = urllib.request.Request(url, headers={"User-Agent": "sign-to-speak-pocket-tts-audition/1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
            while block := response.read(1024 * 1024):
                count += len(block)
                if count > size:
                    raise ValueError(f"Download exceeds pinned size: {url}")
                h.update(block)
                out.write(block)
        if count != size or h.hexdigest() != digest:
            raise ValueError(f"Download size or SHA-256 mismatch: {url}")
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def download_assets(asset_dir: Path, manifest: dict) -> None:
    source = manifest["source"]
    model = manifest["model"]
    root = asset_dir.resolve()
    config_url = f'{source["repository"]}/raw/{source["commit"]}/{source["config_path"]}'
    _download(config_url, root / CONFIG_NAME, source["config_size"], source["config_sha256"])
    for item in model["assets"]:
        url = f'https://huggingface.co/{model["repository"]}/resolve/{model["revision"]}/{item["path"]}'
        _download(url, root / item["path"], item["size"], item["sha256"])
    verify_assets(root, manifest)


def _local_config(files: dict[str, Path], asset_dir: Path) -> Path:
    upstream = files[CONFIG_NAME].read_text(encoding="utf-8")
    root = files[CONFIG_NAME].parent
    sentinel = root / "voice-cloning-disabled.safetensors"
    if sentinel.exists():
        raise ValueError("Cloning sentinel must be absent")
    weights = files["languages/english_18m_2026-10/model.safetensors"]
    tokenizer = files["languages/english_18m_2026-10/tokenizer.model"]
    replacements = {
        "weights_path": sentinel,
        "weights_path_without_voice_cloning": weights,
        "    tokenizer_path": tokenizer,
    }
    config = upstream
    for key, path in replacements.items():
        pattern = rf"(?m)^{re.escape(key)}: hf://[^\n]+$"
        config, count = re.subn(pattern, f"{key}: {path.as_posix()}", config)
        if count != 1:
            raise ValueError(f"Upstream config does not have one expected {key}")
    if "hf://" in config or "https://" in config or "http://" in config:
        raise ValueError("Remote reference remains in local config")
    if "sample_rate: 24000" not in config or "channels: 1" not in config or "n_bins: 4000" not in config:
        raise ValueError("Unexpected audio or tokenizer configuration")
    scratch = asset_dir / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    destination = scratch / LOCAL_CONFIG
    destination.write_text(config, encoding="utf-8")
    return destination


def _source_checkout(source_dir: Path, source_archive: Path, manifest: dict) -> None:
    """Verify downloaded archive and key extracted runtime files before importing."""
    _check_file(source_archive, manifest["source"]["archive_size"], manifest["source"]["archive_sha256"])
    root = source_dir.resolve(strict=True)
    for relative, expected in manifest["source"]["file_sha256"].items():
        path = root / relative
        if not path.resolve().is_relative_to(root):
            raise ValueError("Source path escapes pinned archive root")
        if not path.is_file() or path.is_symlink() or _digest(path) != expected:
            raise ValueError(f"Pocket TTS source file differs from pinned commit: {relative}")


def _guarded_local_resolver(files: dict[str, Path], config_path: Path):
    """Permit only verified local inputs and trigger the no-cloning fallback."""
    root = files[CONFIG_NAME].parent.resolve()
    sentinel = (root / "voice-cloning-disabled.safetensors").resolve()
    allowed = {path.resolve() for path in files.values()}
    allowed.add(config_path.resolve())

    def resolve(source):
        value = str(source)
        if "://" in value:
            raise ValueError("Remote model access is forbidden during audition")
        path = Path(value).resolve()
        if path == sentinel:
            raise FileNotFoundError("Cloning weights deliberately unavailable")
        if path not in allowed or not path.is_file():
            raise ValueError(f"Unapproved model input: {value}")
        return path

    return resolve


def inspect_pcm(audio, sample_rate: int = SAMPLE_RATE) -> tuple[bytes, dict]:
    """Reject silence, NaN, clipping, stereo, or excessive output before WAV write."""
    import numpy as np

    array = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
    if array.ndim == 2 and array.shape[0] == 1:
        array = array[0]
    if array.ndim != 1 or not np.issubdtype(array.dtype, np.floating):
        raise ValueError("Expected mono floating-point PCM")
    if array.size == 0 or array.size > sample_rate * MAX_SECONDS:
        raise ValueError("PCM duration outside audition bounds")
    if not np.isfinite(array).all():
        raise ValueError("PCM contains NaN or infinity")
    peak = float(np.max(np.abs(array)))
    if peak < MIN_PEAK or peak >= 1.0:
        raise ValueError("PCM is silent or clips")
    rms = float(np.sqrt(np.mean(np.square(array.astype(np.float64)))))
    pcm = np.rint(array * 32767.0).astype("<i2", copy=False).tobytes()
    return pcm, {"samples": int(array.size), "duration_seconds": round(array.size / sample_rate, 4), "peak": round(peak, 6), "rms": round(rms, 6)}


def audition(asset_dir: Path, output_dir: Path, source_dir: Path, source_archive: Path, manifest: dict) -> Path:
    files = verify_assets(asset_dir, manifest)
    _source_checkout(source_dir, source_archive, manifest)
    config_path = _local_config(files, asset_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["OMP_NUM_THREADS"] = "2"
    os.environ["MKL_NUM_THREADS"] = "2"
    sys.path.insert(0, str(source_dir.resolve()))
    import numpy as np
    import torch
    from pocket_tts import TTSModel

    local_resolver = _guarded_local_resolver(files, config_path)
    for module_name in (
        "pocket_tts.models.tts_model",
        "pocket_tts.modules.text_conditioner",
        "pocket_tts.utils.config",
        "pocket_tts.utils.utils",
    ):
        setattr(importlib.import_module(module_name), "download_if_necessary", local_resolver)
    if not Path(importlib.import_module("pocket_tts").__file__).resolve().is_relative_to(source_dir.resolve()):
        raise ValueError("Imported Pocket TTS package is not from pinned checkout")
    torch.set_num_threads(2)
    started = time.monotonic()
    model = TTSModel.load_model(config=config_path)
    load_seconds = round(time.monotonic() - started, 3)
    if str(model.device) != "cpu" or model.sample_rate != SAMPLE_RATE or model.has_voice_cloning:
        raise ValueError("Unexpected model device, sample rate, or cloning capability")
    voice = model.get_state_for_audio_prompt(files["languages/english_18m_2026-10/embeddings/alba.safetensors"])
    outputs = []
    for stem, utterance in UTTERANCES:
        started = time.monotonic()
        audio = model.generate_audio(voice, utterance, max_tokens=MAX_TOKENS)
        synthesis_seconds = round(time.monotonic() - started, 3)
        pcm, stats = inspect_pcm(audio)
        path = output_dir / f"{stem}.wav"
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(SAMPLE_RATE)
            out.writeframes(pcm)
        outputs.append({"text": utterance, "file": path.name, "sha256": _digest(path), "synthesis_seconds": synthesis_seconds, **stats})
    try:
        import resource
        peak_rss_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except ImportError:
        peak_rss_kib = None
    result = {
        "status": "synthesized_for_listening_review_only",
        "source_commit": manifest["source"]["commit"],
        "source_version": manifest["source"]["version"],
        "source_archive_sha256": manifest["source"]["archive_sha256"],
        "offline_local_resolver_guard": True,
        "no_voice_cloning_selection": "Pinned local resolver raises FileNotFoundError for an absent cloning sentinel, invoking the official loader fallback to the verified local ungated weights; model.has_voice_cloning is asserted false.",
        "model_revision": manifest["model"]["revision"],
        "language": manifest["model"]["language"],
        "voice": manifest["model"]["voice"],
        "asset_sha256": {item["path"]: item["sha256"] for item in manifest["model"]["assets"]},
        "local_config_sha256": _digest(config_path),
        "sample_rate": SAMPLE_RATE,
        "channels": 1,
        "pcm_format": "signed_16_bit_little_endian",
        "max_tokens_per_utterance": MAX_TOKENS,
        "load_seconds": load_seconds,
        "peak_process_rss_kib": peak_rss_kib,
        "torch_version": torch.__version__,
        "numpy_version": np.__version__,
        "python_version": sys.version.split()[0],
        "offline_environment": {"HF_HUB_OFFLINE": os.environ["HF_HUB_OFFLINE"], "TRANSFORMERS_OFFLINE": os.environ["TRANSFORMERS_OFFLINE"], "CUDA_VISIBLE_DEVICES": os.environ["CUDA_VISIBLE_DEVICES"]},
        "resource_scope": "standard hosted CPU, two torch threads, no user media, five fixed utterances",
        "outputs": outputs,
        "attribution": manifest["attribution"],
    }
    destination = output_dir / "result.json"
    destination.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("download", "verify", "audition"))
    parser.add_argument("--asset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--source-archive", type=Path)
    args = parser.parse_args()
    manifest = _manifest()
    if args.command == "download":
        download_assets(args.asset_dir, manifest)
        print("Three pinned model assets and upstream config verified")
    elif args.command == "verify":
        verify_assets(args.asset_dir, manifest)
        print("All pinned assets verified")
    else:
        if args.output_dir is None or args.source_dir is None or args.source_archive is None:
            parser.error("audition requires --output-dir, --source-dir and --source-archive")
        print(audition(args.asset_dir, args.output_dir, args.source_dir, args.source_archive, manifest))


if __name__ == "__main__":
    main()
