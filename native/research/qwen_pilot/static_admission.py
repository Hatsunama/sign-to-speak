# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["Pillow>=11,<13"]
# ///
"""Prepare source-preserved ASL alphabet photos for a private visual review."""

import argparse
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
from string import ascii_uppercase
import sys
from urllib.request import Request, urlopen
import zipfile
import zlib

from PIL import Image, ImageDraw

from admission_probe import AdmissionError, MIB, bounded_copy


API_URL = "https://www.kaggle.com/api/v1/datasets.DatasetApiService/DownloadDataset"
REQUEST = {
    "ownerSlug": "danrasband",
    "datasetSlug": "asl-alphabet-test",
    "datasetVersionNumber": 1,
}
SOURCE_PAGE = "https://www.kaggle.com/danrasband/asl-alphabet-test/home"
SOURCE_GROUP = "danrasband-asl-alphabet-test-v1"
ANCHOR = "A/A0001_test.jpg"
ANCHOR_BYTES = 10_570
ANCHOR_SHA256 = "4c22cf21532007e15036d69e6d511ed050cea28eeb6bbb65488fe19dd70bba71"
ARCHIVE_CAP = 20 * MIB
EXPANDED_CAP = 64 * MIB
IMAGE_CAP = 128 * 1024
IMAGE_EDGE = 200
CLASSES = (*ascii_uppercase, "del", "nothing", "space")
PER_CLASS = 30
LETTERS = ascii_uppercase.replace("J", "").replace("Z", "")
INDICES = (1, 7, 13, 19, 25)
SELECTED = {f"{letter}/{letter}{index:04d}_test.jpg": letter for letter in LETTERS for index in INDICES}
ROLE = "reference_candidate_not_admitted"


def download_archive(target):
    request = Request(
        API_URL,
        data=json.dumps(REQUEST).encode("utf-8"),
        headers={
            "Accept": "application/zip",
            "Accept-Encoding": "identity",
            "Content-Type": "application/json",
            "User-Agent": "sign-to-speak-admission/1",
        },
        method="POST",
    )
    with urlopen(request, timeout=45) as response:
        if response.status != 200:
            raise AdmissionError("unexpected Kaggle response status")
        declared = response.headers.get("Content-Length")
        if declared is not None:
            if not re.fullmatch(r"[0-9]{1,12}", declared):
                raise AdmissionError("malformed Kaggle Content-Length")
            declared = int(declared)
        return bounded_copy(response, target, ARCHIVE_CAP, declared)


def archive_members(archive):
    entries = archive.infolist()
    if len(entries) > len(CLASSES) * (PER_CLASS + 1):
        raise AdmissionError("too many archive paths")
    members = {}
    seen = set()
    counts = dict.fromkeys(CLASSES, 0)
    expanded = 0
    for member in entries:
        path = member.orig_filename
        directory = member.is_dir()
        folder, separator, name = path.partition("/")
        if (not separator or folder not in counts or path != member.filename
                or (directory and name)
                or (not directory and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\.jpg", name))):
            raise AdmissionError("unexpected archive path")
        key = path.casefold()
        if key in seen:
            raise AdmissionError("duplicate archive path")
        seen.add(key)
        file_type = stat.S_IFMT(member.external_attr >> 16)
        if (member.flag_bits & 1 or member.external_attr & 0x400
                or member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
            raise AdmissionError("unsupported archive member")
        if directory:
            if member.file_size != 0 or file_type not in {0, stat.S_IFDIR}:
                raise AdmissionError("invalid archive directory marker")
            continue
        if file_type not in {0, stat.S_IFREG} or member.external_attr & 0x10:
            raise AdmissionError("archive image is not a regular file")
        if not 0 < member.file_size <= IMAGE_CAP:
            raise AdmissionError("archive member exceeds image byte cap")
        expanded += member.file_size
        if expanded > EXPANDED_CAP:
            raise AdmissionError("expanded archive exceeds byte cap")
        counts[folder] += 1
        members[path] = member
    if any(count != PER_CLASS for count in counts.values()) or not SELECTED.keys() <= members.keys():
        raise AdmissionError("missing expected archive files or class counts differ from version 1")
    return members


def verify_jpeg(data):
    error_message = "expected single-frame 200x200 RGB JPEG"
    try:
        with Image.open(data, formats=("JPEG",)) as image:
            if (image.format != "JPEG" or image.mode != "RGB" or image.size != (IMAGE_EDGE, IMAGE_EDGE)
                    or getattr(image, "n_frames", 1) != 1):
                raise AdmissionError(error_message)
            image.load()
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise AdmissionError(error_message) from error


def read_members(archive, output):
    members = archive_members(archive)
    seen_digests = set()
    samples = {}
    (output / "images").mkdir()
    for source_path, member in members.items():
        original = io.BytesIO()
        with archive.open(member) as source:
            size, digest = bounded_copy(source, original, IMAGE_CAP, member.file_size)
        if source_path == ANCHOR and (size != ANCHOR_BYTES or digest != ANCHOR_SHA256):
            raise AdmissionError("archive does not match the pinned source photo")
        if digest in seen_digests:
            raise AdmissionError("duplicate image bytes in archive")
        seen_digests.add(digest)
        original.seek(0)
        verify_jpeg(original)
        if source_path in SELECTED:
            sample_id = f"static-{digest}"
            path = f"images/{sample_id}.jpg"
            with (output / path).open("xb") as image_file:
                image_file.write(original.getbuffer())
            samples[source_path] = {
                "id": sample_id,
                "label": SELECTED[source_path],
                "sha256": digest,
                "path": path,
                "source_path": source_path,
                "original_bytes": size,
                "source_group": SOURCE_GROUP,
                "role": ROLE,
                "width": IMAGE_EDGE,
                "height": IMAGE_EDGE,
                "split": "train",
            }
    return [samples[path] for path in SELECTED]


def write_sheets(output, samples):
    sheets = output / "contact-sheets"
    sheets.mkdir()
    cols = len(INDICES)
    rows = 4
    tile_width = IMAGE_EDGE + 12
    tile_height = IMAGE_EDGE + 40
    per_sheet = cols * rows
    for start in range(0, len(samples), per_sheet):
        page_samples = samples[start:start + per_sheet]
        with Image.new("RGB", (cols * tile_width, rows * tile_height), "white") as sheet:
            draw = ImageDraw.Draw(sheet)
            for index, sample in enumerate(page_samples):
                row, col = divmod(index, cols)
                x, y = col * tile_width + 6, row * tile_height + 6
                with Image.open(output / sample["path"]) as photo:
                    sheet.paste(photo, (x, y))
                draw.text((x, y + IMAGE_EDGE + 4), sample["source_path"], fill="black")
                draw.text((x, y + IMAGE_EDGE + 19), sample["sha256"][:16], fill="black")
            with (sheets / f"sheet-{start // per_sheet + 1:02d}.png").open("xb") as page:
                sheet.save(page, format="PNG")


def prepare(output):
    output = Path(output).absolute()
    output.mkdir(mode=0o700)
    created = output.lstat()
    canonical_output = output.resolve(strict=True)
    manifest_path = output / "manifest.json"
    staged_manifest = None
    try:
        with io.BytesIO() as payload:
            size, digest = download_archive(payload)
            downloaded_at = datetime.now(timezone.utc).isoformat()
            payload.seek(0)
            try:
                with zipfile.ZipFile(payload) as archive:
                    samples = read_members(archive, output)
            except (zipfile.BadZipFile, UnicodeError, zlib.error) as error:
                raise AdmissionError(f"invalid ZIP archive ({type(error).__name__})") from error

        write_sheets(output, samples)
        manifest = {
            "schema_version": 1,
            "role": ROLE,
            "review_status": "pending_visual_review",
            "source": {
                "title": "ASL Alphabet Test",
                "creator": "Dan Rasband",
                "source_page": SOURCE_PAGE,
                "version": REQUEST["datasetVersionNumber"],
                "license": "CC0-1.0",
                "license_claim": "Publisher card declares CC0: Public Domain.",
                "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "signer_session_independence": "unverified",
                "archive": {
                    "url": API_URL,
                    "method": "POST",
                    "request": dict(REQUEST),
                    "retrieved_at_utc": downloaded_at,
                    "sha256": digest,
                    "bytes": size,
                    "image_count": len(CLASSES) * PER_CLASS,
                },
                "anchor": {"path": ANCHOR, "sha256": ANCHOR_SHA256, "bytes": ANCHOR_BYTES},
            },
            "samples": samples,
        }
        temporary_manifest = output / ".manifest.json.staging"
        with temporary_manifest.open("x", encoding="utf-8", newline="\n") as file:
            json.dump(manifest, file, indent=2)
            file.write("\n")
        staged_manifest = temporary_manifest.lstat()
        temporary_manifest.rename(manifest_path)
    except BaseException as error:
        try:
            try:
                same_directory = (
                    os.path.samestat(created, output.lstat())
                    and output.resolve(strict=True) == canonical_output
                )
            except FileNotFoundError:
                same_directory = False

            published = False
            if same_directory and staged_manifest is not None:
                # The rename may have taken effect before the caller observed an error.
                try:
                    published = os.path.samestat(staged_manifest, manifest_path.lstat())
                except FileNotFoundError:
                    pass
            if same_directory and not published:
                shutil.rmtree(output)
        except Exception as cleanup_error:
            error.add_note(f"Could not clean up {output} ({type(cleanup_error).__name__}: {cleanup_error}).")
        raise
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="Fresh review directory in the private pilot mount")
    args = parser.parse_args()
    try:
        manifest = prepare(args.output)
    except (OSError, AdmissionError) as error:
        print(f"Static photo preparation failed ({error}).", file=sys.stderr)
        for note in getattr(error, "__notes__", ()):
            print(note, file=sys.stderr)
        return 1
    print(f"Prepared {len(manifest['samples'])} static photo candidates for visual review at {args.output}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
