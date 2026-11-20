"""Read one pinned public FSboard development landmark record using bounded HTTPS ranges."""

import argparse
import hashlib
import io
import json
import math
import re
import signal
import sys
import tarfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


DATASET = "googleai/fsboard"
VERSION = 13
SOURCE = "daun_v3/landmarks/daun_v3-train.arrow-00000-of-00100"
STORAGE_PATH = f"/kagglesdsdata/datasets/5151887/12578905/{SOURCE}"
STORAGE_HOST = "storage.googleapis.com"
KAGGLE_API = "https://www.kaggle.com/api/v1/datasets.DatasetApiService/DownloadDataset"
EXPECTED_BYTES = 643_177_994
METADATA_SHA = "826e0b857712b29dc605b814fbf5dea612846d274f43fd369c9be62c926711a6"
ARCHIVE_SHA = "30e21395dddb5e84dd2e062005d12e8035532be680e3b1d12db90e51b636f1cf"
PROPOSAL_SHA = "6f04da3b218f664d8ab4097ab0212fdd135220f89dc8019e9b3b1fb04213e6d9"
CONVERTER_SHA = "3cefababe113e633c732d4f7f471a3d344e3aa5bf0f1af35fc19cf2f972a48e6"
ATTRIBUTION = "Georg et al. (2024), FSboard, https://arxiv.org/abs/2407.15806, CC BY 4.0"
SCOPE = "ORACLE_ANNOTATION_WINDOW_DIAGNOSTIC_ONLY"
SPLIT = "daun_v3-train"
HOLDOUT_SPLIT = "daun_v3-validation"
VIDEO_DIR = f"daun_v3/video_clips/{SPLIT}/"

MIB = 1024**2
MAX_WIRE = 64 * MIB
MAX_REQUESTS = 160
MAX_SECONDS = 120
MAX_READ = 8 * MIB
MAX_RECORD = 16 * MIB
MAX_BATCHES = 4096
MAX_FRAMES = 600
AXES = ("x", "y", "z", "presence", "visibility")
PARTS = {"left_hand": (21,), "right_hand": (21,), "face": (478,), "pose": (25, 33)}
SERIES = {"timestamp"} | {f"{axis}_{part}" for part in PARTS for axis in AXES}
SAFE_VIDEO = re.compile(r"[A-Za-z0-9_.-]+\.mp4")


class ProbeError(ValueError):
    """A fixed message safe for the diagnostic receipt."""


class LandmarkAnatomyError(ProbeError):
    """Unsupported coordinate counts or values in a landmark frame."""


class BudgetExceeded(ProbeError):
    """The probe stopped before completing its requested scan."""


class Budget:
    def __init__(self, max_bytes=MAX_WIRE, max_requests=MAX_REQUESTS, seconds=MAX_SECONDS):
        if not (0 < max_bytes <= MAX_WIRE and 0 < max_requests <= MAX_REQUESTS and 0 < seconds <= MAX_SECONDS):
            raise ProbeError("requested budget exceeds the permitted bounds")
        self.started = time.monotonic()
        self.deadline = self.started + seconds
        self.max_bytes = max_bytes
        self.max_requests = max_requests
        self.reserved_bytes = 0
        self.requests = 0
        self.received_bytes = 0

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise BudgetExceeded("wall-time budget exhausted")
        return remaining

    def reserve(self, size):
        timeout = min(self.remaining(), 10)
        if self.requests >= self.max_requests or self.reserved_bytes + size > self.max_bytes:
            raise BudgetExceeded("HTTP request or range byte budget exhausted")
        self.requests += 1
        self.reserved_bytes += size
        return timeout

    def receipt(self):
        return {
            "max_bytes": self.max_bytes, "max_requests": self.max_requests,
            "max_seconds": self.deadline - self.started,
            "requests_attempted": self.requests, "bytes_reserved": self.reserved_bytes,
            "range_bytes_received": self.received_bytes,
            "elapsed_seconds": round(time.monotonic() - self.started, 3),
        }


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_once(request, timeout):
    try:
        return HTTP_OPENER.open(request, timeout=timeout)
    except HTTPError as response:
        return response


HTTP_OPENER = build_opener(ProxyHandler({}), NoRedirect())


def verify_redirect(response):
    locations = response.headers.get_all("Location", [])
    if response.status not in (301, 302, 303, 307, 308) or len(locations) != 1:
        raise ProbeError("expected exactly one Kaggle storage redirect")
    location = locations[0]
    if "#" in location or any(not 33 <= ord(character) <= 126 for character in location):
        raise ProbeError("malformed Kaggle storage redirect")
    try:
        url = urlsplit(location)
    except ValueError:
        raise ProbeError("malformed Kaggle storage redirect") from None
    if (
        url.scheme != "https"
        or url.netloc not in (STORAGE_HOST, f"{STORAGE_HOST}:443")
        or url.path != STORAGE_PATH
        or not url.query
    ):
        raise ProbeError("redirect does not name the pinned FSboard storage object")
    return location


def resolve_storage(budget):
    body = json.dumps({
        "ownerSlug": "googleai", "datasetSlug": "fsboard", "datasetVersionNumber": VERSION,
        "fileName": SOURCE, "raw": True,
    }).encode()
    request = Request(KAGGLE_API, body, {"Content-Type": "application/json", "Accept-Encoding": "identity"})
    with open_once(request, budget.reserve(0)) as response:
        return verify_redirect(response)


class RangeFile(io.RawIOBase):
    """Seekable single-consumer file for PyArrow's standard IPC reader."""

    def __init__(self, url, size, budget, opener=open_once, *, etag=None):
        self._url = url
        self._size = size
        self._budget = budget
        self._opener = opener
        self._position = 0
        self.etag = etag
        self.ledger = []
        self.failure = None

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self._position

    def seek(self, offset, whence=io.SEEK_SET):
        bases = {io.SEEK_SET: 0, io.SEEK_CUR: self._position, io.SEEK_END: self._size}
        if self.closed or whence not in bases or not 0 <= bases[whence] + offset <= self._size:
            self.failure = ProbeError("Arrow requested an invalid file position")
            raise self.failure
        self._position = bases[whence] + offset
        return self._position

    def read(self, size=-1):
        try:
            if self.closed or not 0 <= size <= MAX_READ:
                raise ProbeError("Arrow requested an unbounded or oversized read")
            size = min(size, self._size - self._position)
            if size == 0:
                return b""
            content = self._range(self._position, size)
            self._position += size
            return content
        except ProbeError as exc:
            self.failure = exc
            raise
        except Exception:
            self.failure = ProbeError("HTTPS range request failed")
            raise self.failure from None

    def readinto(self, buffer):
        content = self.read(len(buffer))
        buffer[: len(content)] = content
        return len(content)

    def _range(self, start, size):
        end = start + size - 1
        timeout = self._budget.reserve(size)
        entry = {"start": start, "end_inclusive": end, "bytes_received": 0, "status": "UNVERIFIED"}
        self.ledger.append(entry)
        headers = {"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"}
        if self.etag is not None:
            headers["If-Match"] = self.etag
        with self._opener(Request(self._url, headers=headers), timeout) as response:
            entry["http_status"] = response.status
            if response.status == 412 and self.etag is not None:
                raise ProbeError("storage ETag precondition failed")
            if (
                response.status != 206
                or response.headers.get_all("Content-Range", []) != [f"bytes {start}-{end}/{self._size}"]
                or response.headers.get_all("Content-Length", []) != [str(size)]
                or response.headers.get_all("Content-Encoding", []) not in ([], ["identity"])
                or response.headers.get_all("Transfer-Encoding", [])
            ):
                raise ProbeError("storage did not honor the exact identity-encoded byte range")
            etags = response.headers.get_all("ETag", [])
            if len(etags) != 1 or not strong_etag(etags[0]):
                raise ProbeError("storage omitted a strong ETag")
            if self.etag is not None and self.etag != etags[0]:
                raise ProbeError("storage ETag changed during the scan")
            self.etag = etags[0]
            chunks = bytearray()
            while len(chunks) < size:
                self._budget.remaining()
                chunk = response.read(min(256 * 1024, size - len(chunks)))
                if not chunk:
                    raise ProbeError("storage range was truncated")
                entry["bytes_received"] += len(chunk)
                self._budget.received_bytes += len(chunk)
                chunks.extend(chunk)
        self._budget.remaining()
        payload = bytes(chunks)
        entry.update(status="VERIFIED", sha256=hashlib.sha256(payload).hexdigest())
        return payload


def strong_etag(value):
    return isinstance(value, str) and re.fullmatch(r'"[\x21\x23-\x7e]{1,128}"', value)


def pinned_bytes(path, cap, pin):
    with path.open("rb") as handle:
        payload = handle.read(cap + 1)
    if len(payload) > cap or hashlib.sha256(payload).hexdigest() != pin:
        raise ProbeError("local provenance input failed its SHA-256 pin")
    return payload


def index_roster(clips):
    index = {}
    for clip in clips:
        filename = clip["full_video_filename"]
        signer = clip["signer_id"]
        prompt = clip["reference"]
        start = clip["annotation_start_full_video_seconds"]
        end = clip["annotation_end_full_video_seconds"]
        raw_start = clip["clip_start_full_video_seconds"]
        raw_end = clip["clip_end_full_video_seconds"]
        source_path = clip["source_path"]
        if clip["split"] != SPLIT:
            raise ProbeError("frozen development publisher split differs from the pinned shard")
        if (
            not SAFE_VIDEO.fullmatch(filename)
            or not source_path.startswith(VIDEO_DIR) or not SAFE_VIDEO.fullmatch(source_path[len(VIDEO_DIR):])
            or not signer or not prompt
            or not all(math.isfinite(value) for value in (raw_start, start, end, raw_end))
            or not 0 <= raw_start <= start < end <= raw_end
        ):
            raise ProbeError("invalid frozen development roster entry")
        key = (f"{filename[:-4]}_{start:.3f}_{end:.3f}.mp4", signer, prompt)
        if key in index:
            raise ProbeError("ambiguous development context in the frozen roster")
        index[key] = clip
    return index


def load_roster(archive_path, metadata_path):
    metadata = json.loads(pinned_bytes(metadata_path, 32 * 1024, METADATA_SHA))
    if (metadata["id"], metadata["ref"], metadata["currentVersionNumber"], metadata["licenseName"]) != (
        5151887, DATASET, VERSION, "Attribution 4.0 International (CC BY 4.0)",
    ):
        raise ProbeError("publisher metadata identity or license mismatch")
    package = pinned_bytes(archive_path, MIB, ARCHIVE_SHA)
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as archive:
        member = archive.getmember("private-proposal.json")
        if not member.isfile() or member.size > MIB:
            raise ProbeError("invalid private proposal archive member")
        with archive.extractfile(member) as stream:
            proposal_bytes = stream.read(MIB + 1)
    if hashlib.sha256(proposal_bytes).hexdigest() != PROPOSAL_SHA:
        raise ProbeError("private proposal failed its SHA-256 pin")
    proposal = json.loads(proposal_bytes)
    development = proposal["clips"]["development"]
    holdout = proposal["clips"]["holdout"]
    if (proposal["dataset"]["id"], proposal["dataset"]["version"], len(development), len(holdout)) != (
        DATASET, VERSION, 32, 64,
    ):
        raise ProbeError("frozen development and holdout roster mismatch")
    if proposal["splits"]["development"]["publisher_split"] != SPLIT or {row["split"] for row in holdout} != {HOLDOUT_SPLIT}:
        raise ProbeError("frozen development or holdout publisher split differs from the pinned policy")
    for key in ("signer_id", "full_video_filename", "reference", "source_path"):
        if {row[key].casefold() for row in development} & {row[key].casefold() for row in holdout}:
            raise ProbeError("frozen development roster overlaps the holdout")
    return index_roster(development)


def sequence_example_type():
    """Load the TensorFlow proto3 wire schema into a private protobuf descriptor pool."""
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    # tensorflow/core/example/{feature,example}.proto at v2.19.0.
    descriptor = descriptor_pb2.FileDescriptorProto(name="sequence_example.proto", package="tensorflow", syntax="proto3")
    field_type = descriptor_pb2.FieldDescriptorProto

    def field(message, name, number, kind, *, repeated=False, type_name="", oneof=None):
        target = message.field.add(name=name, number=number, type=kind)
        target.label = field_type.LABEL_REPEATED if repeated else field_type.LABEL_OPTIONAL
        if type_name:
            target.type_name = type_name
        if oneof is not None:
            target.oneof_index = oneof

    kinds = (("BytesList", field_type.TYPE_BYTES), ("FloatList", field_type.TYPE_FLOAT),
             ("Int64List", field_type.TYPE_INT64))
    for name, kind in kinds:
        field(descriptor.message_type.add(name=name), "value", 1, kind, repeated=True)
    feature = descriptor.message_type.add(name="Feature")
    feature.oneof_decl.add(name="kind")
    for number, ((name, _), key) in enumerate(zip(kinds, ("bytes_list", "float_list", "int64_list")), 1):
        field(feature, key, number, field_type.TYPE_MESSAGE, type_name=f".tensorflow.{name}", oneof=0)
    feature_list = descriptor.message_type.add(name="FeatureList")
    field(feature_list, "feature", 1, field_type.TYPE_MESSAGE, repeated=True, type_name=".tensorflow.Feature")
    for name, key, value in (("Features", "feature", "Feature"), ("FeatureLists", "feature_list", "FeatureList")):
        message = descriptor.message_type.add(name=name)
        entry = message.nested_type.add(name="Entry")
        entry.options.map_entry = True
        field(entry, "key", 1, field_type.TYPE_STRING)
        field(entry, "value", 2, field_type.TYPE_MESSAGE, type_name=f".tensorflow.{value}")
        field(message, key, 1, field_type.TYPE_MESSAGE, repeated=True, type_name=f".tensorflow.{name}.Entry")
    sequence = descriptor.message_type.add(name="SequenceExample")
    field(sequence, "context", 1, field_type.TYPE_MESSAGE, type_name=".tensorflow.Features")
    field(sequence, "feature_lists", 2, field_type.TYPE_MESSAGE, type_name=".tensorflow.FeatureLists")
    pool = descriptor_pool.DescriptorPool()
    pool.Add(descriptor)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName("tensorflow.SequenceExample"))


def context_scalar(context, name, kind):
    if name not in context or context[name].WhichOneof("kind") != kind:
        raise ProbeError(f"invalid SequenceExample context field {name}")
    values = getattr(context[name], kind).value
    if len(values) != 1:
        raise ProbeError(f"expected a scalar SequenceExample context field {name}")
    return values[0]


def signer_scalar(context):
    if "signer_id" not in context:
        raise ProbeError("missing SequenceExample signer_id")
    kind = context["signer_id"].WhichOneof("kind")
    if kind not in ("bytes_list", "int64_list"):
        raise ProbeError("unsupported SequenceExample signer_id wire kind")
    raw = context_scalar(context, "signer_id", kind)
    signer = raw.decode("utf-8") if kind == "bytes_list" else str(raw)
    if not signer:
        raise ProbeError("empty SequenceExample signer_id")
    return signer, kind


def checked_series(feature_lists, nframes):
    if set(feature_lists) - SERIES or "timestamp" not in feature_lists:
        raise ProbeError("unexpected landmark feature-list schema")
    series = {}
    for key, feature_list in feature_lists.items():
        if len(feature_list.feature) != nframes:
            raise ProbeError("landmark feature-list length differs from frame count")
        kind = "int64_list" if key == "timestamp" else "float_list"
        frames = []
        for frame in feature_list.feature:
            actual = frame.WhichOneof("kind")
            if actual is None:
                frames.append(())
            elif actual == kind:
                frames.append(tuple(getattr(frame, kind).value))
            else:
                raise ProbeError("invalid scalar kind in landmark feature list")
        series[key] = frames
    return series


def landmark_frames(series, nframes, frame_rate):
    if not (2 <= nframes <= MAX_FRAMES and math.isfinite(frame_rate) and 1 <= frame_rate <= 120):
        raise ProbeError("record frame count or frame rate is outside the diagnostic bounds")
    if set(series) - SERIES or "timestamp" not in series or any(len(frames) != nframes for frames in series.values()):
        raise ProbeError("unexpected landmark feature-list schema or length")
    timestamps = []
    for values in series["timestamp"]:
        if len(values) != 1 or type(values[0]) is not int or values[0] < 0:
            raise ProbeError("invalid measured frame timestamp")
        timestamps.append(values[0])
    # Corroborate microseconds over the measured span, allowing uneven frame intervals.
    mean_period = (timestamps[-1] - timestamps[0]) / (nframes - 1)
    period = 1_000_000 / frame_rate
    if (
        any(end <= start for start, end in zip(timestamps, timestamps[1:]))
        or not 0.8 * period <= mean_period <= 1.2 * period
    ):
        raise ProbeError("timestamps are nonmonotonic or cadence does not support microseconds")

    absent = [()] * nframes
    groups = {}
    for part, expected in PARTS.items():
        source = [series.get(f"{axis}_{part}", absent) for axis in AXES]
        groups[part] = []
        for values in zip(*source):
            counts = [len(points) for points in values]
            if not any(counts):
                groups[part].append(None)
                continue
            count = counts[0]
            if (
                count not in expected or counts[:3] != [count] * 3
                or any(size not in (0, count) for size in counts[3:])
                or not all(math.isfinite(point) for points in values for point in points)
            ):
                raise LandmarkAnatomyError("invalid anatomy, incomplete coordinate axes, or nonfinite landmark")
            groups[part].append(dict(zip(AXES, [points or None for points in values])))
    return {"timestamp_us": timestamps, "groups": groups}


def decode_landmarks(example):
    context = example.context.feature
    frames = context_scalar(context, "num_frames", "int64_list")
    width = context_scalar(context, "image/width", "int64_list")
    height = context_scalar(context, "image/height", "int64_list")
    rate = context_scalar(context, "image/frame_rate", "float_list")
    if not (2 <= frames <= MAX_FRAMES and 0 < width <= 8192 and 0 < height <= 8192):
        raise ProbeError("landmark frame count or image dimensions are outside the supported bounds")
    landmarks = landmark_frames(checked_series(example.feature_lists.feature_list, frames), frames, rate)
    return {
        "image_width": width, "image_height": height, "publisher_frame_rate": rate,
        "frame_count": frames, "landmarks": landmarks,
    }


def observed_axis_lengths(landmarks):
    return {
        part: {
            axis: sorted({len(frame[axis]) if frame and frame[axis] else 0 for frame in values})
            for axis in AXES
        }
        for part, values in landmarks["groups"].items()
    }


def scan_first(handle, roster, receipt, *, start_at=0):
    import pyarrow as pa

    sequence_type = sequence_example_type()
    if handle.read(6) != b"ARROW1":
        raise ProbeError("pinned shard lacks Arrow IPC file magic")
    handle.seek(0)
    try:
        with pa.PythonFile(handle, mode="r") as source:
            reader = pa.ipc.open_file(source, options=pa.ipc.IpcReadOptions(use_threads=False))
            if reader.schema.names != ["serialized"] or reader.schema.field(0).type != pa.binary():
                raise ProbeError("unexpected Arrow serialized column schema")
            count = reader.num_record_batches
            receipt["record_batches"] = count
            if not 0 < count <= MAX_BATCHES or not 0 <= start_at <= count:
                raise ProbeError("Arrow batch count or resume cursor is outside the supported bounds")
            for index in range(start_at, count):
                batch = reader.get_batch(index)
                if batch.num_rows != 1:
                    raise ProbeError("expected one publisher SequenceExample per Arrow batch")
                batch.validate(full=True)
                column = batch.column(0)
                data_buffer = column.buffers()[2]
                if column.null_count or data_buffer is None or not 0 < data_buffer.size <= MAX_RECORD:
                    raise ProbeError("publisher SequenceExample size is outside the supported bound")
                raw = column[0].as_py()
                if not raw:
                    raise ProbeError("empty publisher SequenceExample")
                example = sequence_type.FromString(raw)
                context = example.context.feature
                signer, signer_kind = signer_scalar(context)
                kinds = receipt.setdefault("observed_signer_wire_kinds", {})
                kinds[signer_kind] = kinds.get(signer_kind, 0) + 1
                video = context_scalar(context, "video_name", "bytes_list").decode("utf-8")
                prompt = context_scalar(context, "prompt", "bytes_list").decode("utf-8")
                key = (video, signer, prompt)
                selected = roster.get(key)
                if selected is not None or "first_decoded_schema" not in receipt:
                    try:
                        decoded = decode_landmarks(example)
                    except BudgetExceeded:
                        raise
                    except ProbeError:
                        if selected is not None:
                            raise
                        receipt["first_decoded_schema"] = {
                            "status": "UNSUPPORTED", "signer_wire_kind": signer_kind,
                        }
                    else:
                        counts = observed_axis_lengths(decoded["landmarks"])
                        receipt.setdefault("first_decoded_schema", {
                            "signer_wire_kind": signer_kind, "group_axis_lengths": counts,
                            "frame_count": decoded["frame_count"], "publisher_frame_rate": decoded["publisher_frame_rate"],
                        })
                receipt["records_examined"] = index - start_at + 1
                if selected is None:
                    receipt["next_batch_index"] = index + 1
                    continue
                receipt["matched_batch_index"] = index
                receipt["development_roster_index"] = tuple(roster).index(key)
                receipt["matched_signer_wire_kind"] = signer_kind
                receipt["matched_group_axis_lengths"] = counts
                return {
                    "scope": SCOPE, "record_sha256": hashlib.sha256(raw).hexdigest(),
                    "annotation_window_full_video_seconds": [
                        selected["annotation_start_full_video_seconds"], selected["annotation_end_full_video_seconds"],
                    ],
                    "raw_clip_window_full_video_seconds": [
                        selected["clip_start_full_video_seconds"], selected["clip_end_full_video_seconds"],
                    ],
                    **decoded,
                }
    except ProbeError:
        raise
    except Exception:
        if handle.failure is not None:
            raise handle.failure from None
        raise ProbeError("Arrow IPC decompression, schema, or SequenceExample decoding failed") from None
    return None


def write_private_json(directory, name, document):
    path = directory / name
    incomplete = path.with_suffix(".tmp")
    with incomplete.open("x", encoding="utf-8") as stream:
        json.dump(document, stream, allow_nan=False, separators=(",", ":"))
        stream.write("\n")
    incomplete.rename(path)


def read_resume(path):
    with path.open("rb") as stream:
        payload = stream.read(MIB + 1)
    if len(payload) > MIB:
        raise ProbeError("resume receipt exceeds its size cap")
    resume = json.loads(payload)
    if any(resume.get(key) != expected for key, expected in {
        "status": "INCOMPLETE", "dataset": DATASET, "version": VERSION, "object": SOURCE,
        "object_bytes": EXPECTED_BYTES, "storage_host": STORAGE_HOST, "storage_path": STORAGE_PATH,
        "frozen_source_archive_sha256": ARCHIVE_SHA, "frozen_private_proposal_sha256": PROPOSAL_SHA,
        "publisher_metadata_sha256": METADATA_SHA, "split": SPLIT,
    }.items()):
        raise ProbeError("resume receipt does not identify an incomplete scan of the pinned source")
    next_batch = resume.get("next_batch_index")
    etag = resume.get("etag")
    if type(next_batch) is not int or not 0 <= next_batch <= MAX_BATCHES or not strong_etag(etag):
        raise ProbeError("resume receipt does not contain a usable cursor and strong ETag")
    start_batch = resume.get("start_batch_index")
    records_examined = resume.get("records_examined")
    if (
        type(start_batch) is not int or not 0 <= start_batch <= next_batch
        or type(records_examined) is not int or records_examined != next_batch - start_batch
    ):
        raise ProbeError("resume receipt cursor disagrees with recorded scan progress")
    return next_batch, etag


def run(archive, metadata, output, budget, *, resume_path=None):
    output.mkdir(mode=0o700)
    receipt = {
        "status": "FAILED", "scope": SCOPE, "dataset": DATASET, "version": VERSION,
        "dataset_id": 5151887, "attribution": ATTRIBUTION, "object": SOURCE, "object_bytes": EXPECTED_BYTES,
        "storage_host": STORAGE_HOST, "storage_path": STORAGE_PATH, "etag": None, "ranges": [],
        "frozen_source_archive_sha256": ARCHIVE_SHA, "frozen_private_proposal_sha256": PROPOSAL_SHA,
        "publisher_metadata_sha256": METADATA_SHA, "publisher_converter_reference_sha256": CONVERTER_SHA,
        "split": SPLIT, "records_examined": 0, "start_batch_index": 0, "next_batch_index": 0,
        "selection": "first context matching one frozen development roster entry",
    }
    handle = None
    try:
        signal.setitimer(signal.ITIMER_REAL, budget.remaining())
        roster = load_roster(archive, metadata)
        start_at, etag = read_resume(resume_path) if resume_path is not None else (0, None)
        receipt.update(start_batch_index=start_at, next_batch_index=start_at)
        location = resolve_storage(budget)
        with RangeFile(location, EXPECTED_BYTES, budget, etag=etag) as handle:
            features = scan_first(handle, roster, receipt, start_at=start_at)
        budget.remaining()
        signal.setitimer(signal.ITIMER_REAL, 0)
        if features is None:
            receipt.update(status="INCOMPLETE", reason="no development context matched in the pinned shard")
        else:
            write_private_json(output, "landmarks.json", features)
            receipt.update(status="SUCCESS", record_sha256=features["record_sha256"],
                           next_batch_index=receipt["matched_batch_index"] + 1)
    except BudgetExceeded as exc:
        receipt.update(status="INCOMPLETE", reason=str(exc))
    except ProbeError as exc:
        receipt["reason"] = str(exc)
    except Exception:
        receipt["reason"] = "probe could not initialize its dependencies, open verified inputs, or finish the scan"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        if receipt["status"] == "INCOMPLETE":
            # A match remains pending until publication, and the timer can interrupt either scan update.
            receipt["records_examined"] = receipt["next_batch_index"] - receipt["start_batch_index"]
        if handle is not None:
            receipt.update(etag=handle.etag, ranges=handle.ledger)
        receipt["budget"] = budget.receipt()
        write_private_json(output, "receipt.json", receipt)
    return receipt["status"]


def timeout_handler(signum, frame):
    raise BudgetExceeded("wall-time budget exhausted")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r3-archive", type=Path, required=True)
    parser.add_argument("--publisher-metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New private directory for the receipt and features")
    parser.add_argument("--resume-receipt", type=Path, help="Prior incomplete receipt for this exact storage object")
    parser.add_argument("--max-bytes", type=int, default=MAX_WIRE)
    parser.add_argument("--max-requests", type=int, default=MAX_REQUESTS)
    parser.add_argument("--seconds", type=int, default=MAX_SECONDS)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("the network probe requires the Linux cloud runner")
    import os
    import resource

    # Bound native decompression allocation and keep NumPy's startup single-threaded.
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["ARROW_DEFAULT_MEMORY_POOL"] = "system"
    limit = 2 * 1024 * MIB
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    bound = min(candidate for candidate in (soft, hard, limit) if candidate != resource.RLIM_INFINITY)
    resource.setrlimit(resource.RLIMIT_AS, (bound, hard))
    os.umask(0o077)
    signal.signal(signal.SIGALRM, timeout_handler)
    try:
        budget = Budget(args.max_bytes, args.max_requests, args.seconds)
        status = run(args.r3_archive, args.publisher_metadata, args.output, budget, resume_path=args.resume_receipt)
    except ProbeError as exc:
        parser.error(str(exc))
    except OSError:
        parser.error("could not create or finish the new private output directory")
    print(f"FSboard landmark diagnostic: {status}")
    return {"SUCCESS": 0, "FAILED": 1, "INCOMPLETE": 2}[status]


if __name__ == "__main__":
    sys.exit(main())

