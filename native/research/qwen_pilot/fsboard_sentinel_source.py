"""Build and verify the private FSBoard v2 sentinel source archive."""

import argparse
import ast
import gzip
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import tokenize


SUITE = (
    "test_fsboard_task_admission",
    "test_fsboard_task_protocol",
    "test_fsboard_training_intake",
    "test_fsboard_admission",
    "test_fsboard_evaluate",
)
SEED_SOURCES = (
    "fsboard_admission.py",
    "fsboard_evaluate.py",
    "fsboard_task_admission.py",
    "fsboard_task_protocol.py",
    "fsboard_training_intake.py",
    "paired_experiment.py",
    "train_qwen_pilot.py",
    "video_orientation.py",
    *(f"{module}.py" for module in SUITE),
)
RELEASE_SOURCES = frozenset((*SEED_SOURCES, "test_train_qwen_temporal.py", "test_train_qwen_pilot.py"))
PRIVATE_SHA256 = {
    "private-proposal.json": "abc506b6edc91d23fbdf416b36f38fd0c739d5e0c2bfff891fa41aef64bc667c",
    "fsboard-eval-v3-approved.json": "5be4dd8a5d5225c8131eab855f521a83382a4da3f18804170e917ff381ebd8ac",
}
MANIFEST = "SOURCE-MANIFEST.json"


def read_input(root, path):
    path = Path(os.path.abspath(path))
    if not path.is_relative_to(root):
        raise ValueError(f"Input is outside the source root: {path}")
    for candidate in (path, *path.parents):
        status = candidate.lstat()
        if stat.S_ISLNK(status.st_mode) or getattr(status, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"Symlink or reparse point is not a release input: {candidate}")
        if candidate == root:
            break
    if not path.is_file():
        raise ValueError(f"Input is not a regular file: {path}")
    return path.read_bytes()


def read_source(root, name):
    data = read_input(root, root / name)
    # One leading UTF-8 BOM is accepted and removed. Python's source cookie
    # must also declare UTF-8 if present.
    source = data.decode("utf-8-sig", errors="strict").replace("\r\n", "\n").replace("\r", "\n")
    encoding, _ = tokenize.detect_encoding(io.BytesIO(source.encode("utf-8")).readline)
    if encoding != "utf-8":
        raise ValueError(f"Python source declares a non-UTF-8 encoding: {name}")
    return source


def collect_sources(root):
    pending = list(SEED_SOURCES)
    sources = {}
    while pending:
        name = pending.pop()
        if name in sources:
            continue
        try:
            source = read_source(root, name)
        except FileNotFoundError as error:
            raise ValueError(f"Missing required local source: {name}") from error
        sources[name] = source.encode("utf-8")
        imports = set()
        for node in ast.walk(ast.parse(source, filename=name)):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    raise ValueError(f"Relative import is unsupported in the flat archive: {name}")
                imports.add(node.module.split(".", 1)[0])
        for module in sorted(imports):
            dependency = f"{module}.py"
            if dependency in RELEASE_SOURCES:
                pending.append(dependency)
            elif os.path.lexists(root / dependency) or os.path.lexists(root / module):
                raise ValueError(f"Unexpected local dependency in {name}: {dependency}")
    if sources.keys() != RELEASE_SOURCES:
        raise ValueError(f"Release dependency closure changed: {sorted(RELEASE_SOURCES - sources.keys())}")
    return sources


def read_private(root, path, name):
    if path.name != name:
        raise ValueError(f"Expected private input named {name}")
    data = read_input(root, path)
    if sha256(data).hexdigest() != PRIVATE_SHA256[name]:
        raise ValueError(f"SHA-256 mismatch for {name}")
    return data


def json_bytes(value):
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def archive_source(files):
    manifest = [
        {"file": name, "bytes": len(data), "sha256": sha256(data).hexdigest()}
        for name, data in sorted(files.items())
    ]
    contents = {**files, MANIFEST: json_bytes(manifest)}
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", mtime=0, fileobj=buffer) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, data in sorted(contents.items()):
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
    return buffer.getvalue(), manifest


def check_unicode_fixture(files):
    source = files["test_fsboard_evaluate.py"].decode("utf-8", errors="strict")
    if "\uff21" not in source or "Stra\u00dfe" not in source:
        raise ValueError("Unicode fixture lost the fullwidth A or Straße during packaging")


ISOLATED_RUNNER = """
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import unittest

work = Path.cwd()
base_dirs = {Path(sys.base_prefix).resolve(), Path(sys.prefix).resolve()}
sys.path[:] = [str(work), *[entry for entry in sys.path if entry and any(
    Path(entry).resolve().is_relative_to(base) for base in base_dirs
)]]
modules = json.loads(sys.argv[1])
suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
result = unittest.TextTestRunner(verbosity=1).run(suite)
if not result.wasSuccessful() or result.skipped or not result.testsRun:
    sys.exit(1)
for name in json.loads(sys.argv[2]):
    module = sys.modules.get(name)
    if module is None or Path(module.__file__).resolve() != work / (name + '.py'):
        raise RuntimeError(f'Release module did not load from the archive: {name}')
if 'torch' in sys.modules:
    raise RuntimeError('The sentinel source suite loaded torch')
print(json.dumps({
    'test_modules': modules,
    'tests': result.testsRun,
    'failures': len(result.failures),
    'errors': len(result.errors),
    'skips': len(result.skipped),
    'python': platform.python_version(),
    'packages': {name: importlib.metadata.version(name) for name in ('av', 'Pillow')},
}))
"""


def validate_archive(data, files, manifest):
    expected = {**files, MANIFEST: json_bytes(manifest)}
    with tempfile.TemporaryDirectory(prefix="fsboard-sentinel-source-") as directory:
        work = Path(directory).resolve()
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            members = archive.getmembers()
            if len(members) > 30 or sum(member.size for member in members) >= 3_000_000:
                raise ValueError("Archive exceeds the cloud sentinel source limits")
            if [member.name for member in members] != sorted(expected):
                raise ValueError("Unexpected archive member list")
            contents = {}
            for member in members:
                if not member.isfile() or member.size != len(expected[member.name]):
                    raise ValueError(f"Archive member differs from verified input: {member.name}")
                with archive.extractfile(member) as stream:
                    source = stream.read()
                if source != expected[member.name]:
                    raise ValueError(f"Archive member differs from verified input: {member.name}")
                (work / member.name).write_bytes(source)
                contents[member.name] = source
        check_unicode_fixture(contents)
        process = subprocess.run(
            [sys.executable, "-I", "-B", "-X", "utf8", "-c", ISOLATED_RUNNER, json.dumps(SUITE),
             json.dumps(sorted(Path(name).stem for name in RELEASE_SOURCES))],
            cwd=work, capture_output=True, encoding="utf-8", timeout=180,
        )
        if process.returncode:
            raise ValueError(f"Isolated source suite failed (exit {process.returncode}):\n{process.stderr}")
        return json.loads(process.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--proposal", required=True, type=Path)
    parser.add_argument("--approved-receipt", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    arguments = parser.parse_args()

    root = Path(os.path.abspath(arguments.source_root))
    output = arguments.output_dir.absolute()
    if os.path.lexists(output):
        parser.error(f"Output directory already exists: {output}")
    if not root.is_dir() or root.resolve() != root:
        parser.error(f"Source root must be a real directory: {root}")

    files = collect_sources(root)
    files["private-proposal.json"] = read_private(root, arguments.proposal, "private-proposal.json")
    files["fsboard-eval-v3-approved.json"] = read_private(root, arguments.approved_receipt, "fsboard-eval-v3-approved.json")
    archive, manifest = archive_source(files)
    suite = validate_archive(archive, files, manifest)

    output.mkdir()
    archive_path = output / f"{output.name}.tar.gz"
    with archive_path.open("xb") as stream:
        stream.write(archive)
    readback = archive_path.read_bytes()
    if readback != archive:
        raise ValueError("Published archive differs from the verified bytes")
    report = {
        "archive": archive_path.name,
        "bytes": len(readback),
        "sha256": sha256(readback).hexdigest(),
        "files": manifest,
        "private_input_sha256": PRIVATE_SHA256,
        "unicode_fixture_verified": True,
        "isolated_archive_suite": suite,
    }
    with (output / "source-receipt.json").open("xb") as stream:
        stream.write(json_bytes(report))
    print(json_bytes(report).decode("utf-8"), end="")


if __name__ == "__main__":
    main()
