"""Contract tests and an explicitly selected cloud wheel integration test."""

import hashlib
import io
import json
import sys
import tarfile
import unittest
from email.message import Message
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import publisher_landmark_probe as probe


class Response(io.BytesIO):
    def __init__(self, body, status=206, **headers):
        super().__init__(body)
        self.status = status
        self.headers = Message()
        for name, value in headers.items():
            self.headers[name.replace("_", "-")] = value


class FakeStorage:
    def __init__(self, body, **headers):
        self.body = body
        self.headers = headers
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        start, end = map(int, request.get_header("Range").split("=")[1].split("-"))
        if end >= len(self.body):
            raise AssertionError("range past EOF")
        size = end - start + 1
        headers = {
            "Content_Range": f"bytes {start}-{end}/{len(self.body)}",
            "Content_Length": str(size),
            "ETag": '"immutable"',
            **self.headers,
        }
        return Response(self.body[start : end + 1], **headers)


def clip():
    return {
        "signer_id": "0007",
        "reference": "private-target",
        "split": "daun_v3-train",
        "source_path": "daun_v3/video_clips/daun_v3-train/raw.mp4",
        "full_video_filename": "take.mp4",
        "clip_start_full_video_seconds": 0.8,
        "clip_end_full_video_seconds": 2.2,
        "annotation_start_full_video_seconds": 1.0,
        "annotation_end_full_video_seconds": 2.0,
    }


def series():
    values = {"timestamp": [(28,), (33_500,), (66_972,)]}
    for part, count in (("left_hand", 21), ("right_hand", 21), ("face", 478), ("pose", 25)):
        for axis in ("x", "y", "z", "presence", "visibility"):
            values[f"{axis}_{part}"] = [(0.7,) * count] * 3
    for axis in ("x", "y", "z", "presence", "visibility"):
        values[f"{axis}_right_hand"][1] = ()
    return values


def feature(kind, values):
    return SimpleNamespace(WhichOneof=lambda _: kind, **{kind: SimpleNamespace(value=values)})


def example_fixture(*, selected, malformed=False):
    context = {
        "video_name": feature("bytes_list", (b"take_1.000_2.000.mp4",)),
        "signer_id": feature("bytes_list", (b"0007",)),
        "prompt": feature("bytes_list", (b"private-target" if selected else b"private-nonroster-target",)),
        "num_frames": feature("int64_list", (3,)),
        "image/width": feature("int64_list", (640,)),
        "image/height": feature("int64_list", (480,)),
        "image/frame_rate": feature("float_list", (29.875,)),
    }
    values = series()
    if malformed:
        values["x_pose"][1] = (0.7,) * 24
    lists = {
        key: SimpleNamespace(feature=[
            feature("int64_list" if key == "timestamp" else "float_list", frame)
            for frame in frames
        ])
        for key, frames in values.items()
    }
    return SimpleNamespace(
        context=SimpleNamespace(feature=context), feature_lists=SimpleNamespace(feature_list=lists)
    )


def fake_arrow(examples):
    batches = []
    for raw in examples:
        column = MagicMock(null_count=0)
        column.buffers.return_value = (None, None, SimpleNamespace(size=len(raw)))
        column.__getitem__.return_value.as_py.return_value = raw
        batch = Mock(num_rows=1)
        batch.column.return_value = column
        batches.append(batch)
    arrow = MagicMock()
    schema = SimpleNamespace(names=["serialized"], field=lambda _: SimpleNamespace(type=arrow.binary()))
    reader = SimpleNamespace(schema=schema, num_record_batches=len(batches), get_batch=batches.__getitem__)
    arrow.ipc.open_file.return_value = reader
    sequence_type = SimpleNamespace(FromString=examples.__getitem__)
    return patch.dict(sys.modules, {"pyarrow": arrow}), patch.object(probe, "sequence_example_type", return_value=sequence_type)


def assert_matched_batch_resumes(test, data, matched_raw):
    storage = FakeStorage(data)
    range_file = probe.RangeFile
    budget = probe.Budget()
    scanner = probe.scan_first

    def expire_after_match(*args, **kwargs):
        result = scanner(*args, **kwargs)
        test.assertIsNotNone(result)
        budget.remaining = Mock(side_effect=probe.BudgetExceeded("wall-time budget exhausted"))
        return result

    def open_range(url, size, active_budget, *, etag):
        return range_file(url, size, active_budget, storage, etag=etag)

    with (
        patch.object(probe, "EXPECTED_BYTES", len(data)),
        patch.object(probe.signal, "setitimer", create=True),
        patch.object(probe.signal, "ITIMER_REAL", 0, create=True),
        patch.object(probe, "load_roster", return_value=probe.index_roster([clip()])),
        patch.object(probe, "resolve_storage", return_value="https://example.invalid/object"),
        patch.object(probe, "RangeFile", side_effect=open_range),
        patch.object(probe, "write_private_json") as publish,
    ):
        with patch.object(probe, "scan_first", side_effect=expire_after_match):
            test.assertEqual(probe.run(None, None, Mock(), budget), "INCOMPLETE")
        test.assertEqual([call.args[1] for call in publish.call_args_list], ["receipt.json"])
        receipt = publish.call_args.args[2]
        test.assertEqual(receipt["matched_batch_index"], 1)
        test.assertEqual(receipt["next_batch_index"], 1)
        test.assertEqual(receipt["records_examined"], 1)
        test.assertEqual(receipt["start_batch_index"], 0)
        cursor = Mock()
        cursor.open.return_value = io.BytesIO(json.dumps(receipt).encode())
        request_count = len(storage.requests)
        publish.reset_mock()

        test.assertEqual(probe.run(None, None, Mock(), probe.Budget(), resume_path=cursor), "SUCCESS")
        test.assertEqual(storage.requests[request_count].get_header("If-match"), '"immutable"')
        test.assertEqual([call.args[1] for call in publish.call_args_list], ["landmarks.json", "receipt.json"])
        features = publish.call_args_list[0].args[2]
        test.assertEqual(features["record_sha256"], hashlib.sha256(matched_raw).hexdigest())
        receipt = publish.call_args.args[2]
        test.assertEqual(receipt["start_batch_index"], 1)
        test.assertEqual(receipt["matched_batch_index"], 1)
        test.assertEqual(receipt["next_batch_index"], 2)


class ContractTests(unittest.TestCase):
    def test_redirect_pins_object_and_never_reflects_signed_query(self):
        secret = "no-print-query"
        base = "https://storage.googleapis.com:443"
        signed = f"{base}{probe.STORAGE_PATH}?secret={secret}"
        actual = probe.verify_redirect(Response(b"", status=302, Location=signed))
        self.assertEqual(actual, signed)
        for bad in (signed.replace("/12578905/", "/12578906/"), signed.replace(base, "https://evil.invalid")):
            with self.assertRaises(probe.ProbeError) as raised:
                probe.verify_redirect(Response(b"", status=302, Location=bad))
            self.assertNotIn(secret, str(raised.exception))

    def test_random_access_checks_identity_and_preserves_range_receipts(self):
        storage = FakeStorage(bytes(range(32)))
        budget = probe.Budget(max_bytes=8, max_requests=2)
        with probe.RangeFile("https://example.invalid/object", 32, budget, storage) as handle:
            self.assertEqual(handle.seek(-4, io.SEEK_END), 28)
            self.assertEqual(handle.read(4), bytes(range(28, 32)))
            self.assertEqual(handle.read(1), b"")
            handle.seek(2)
            self.assertEqual(handle.read(4), bytes(range(2, 6)))
            self.assertEqual(storage.requests[1].get_header("If-match"), '"immutable"')
            handle.seek(8)
            with self.assertRaises(probe.BudgetExceeded):
                handle.read(1)
            self.assertEqual(len(storage.requests), 2)
            self.assertEqual(handle.ledger[0]["sha256"], hashlib.sha256(bytes(range(28, 32))).hexdigest())
            self.assertEqual(handle.ledger[1]["status"], "VERIFIED")

    def test_ignored_range_bad_bounds_and_truncation_fail_closed(self):
        for response in (
            Response(b"abcd", status=200, Content_Length="4", ETag='"v"'),
            Response(b"abcd", Content_Range="bytes 1-4/32", Content_Length="4", ETag='"v"'),
            Response(b"abc", Content_Range="bytes 0-3/32", Content_Length="4", ETag='"v"'),
            Response(b"abcd", Content_Range="bytes 0-3/32", Content_Length="4", ETag='W/"v"'),
            Response(b"abcd", Content_Range="bytes 0-3/32", Content_Length="4", ETag='"v"', Content_Encoding="gzip"),
        ):
            budget = probe.Budget()
            with probe.RangeFile("https://example.invalid", 32, budget, lambda *_: response) as handle:
                with self.assertRaises(probe.ProbeError):
                    handle.read(4)
                self.assertNotEqual(handle.ledger[-1]["status"], "VERIFIED")

    def test_representation_change_fails_closed(self):
        storage = FakeStorage(bytes(range(32)))
        with probe.RangeFile("https://example.invalid", 32, probe.Budget(), storage) as handle:
            handle.read(4)
            storage.headers["ETag"] = '"changed"'
            with self.assertRaisesRegex(probe.ProbeError, "ETag"):
                handle.read(4)

    def test_resume_pins_source_identity_cursor_and_first_request_etag(self):
        record = {
            "status": "INCOMPLETE", "dataset": probe.DATASET, "version": probe.VERSION,
            "object": probe.SOURCE, "object_bytes": probe.EXPECTED_BYTES, "storage_host": probe.STORAGE_HOST,
            "storage_path": probe.STORAGE_PATH, "frozen_source_archive_sha256": probe.ARCHIVE_SHA,
            "frozen_private_proposal_sha256": probe.PROPOSAL_SHA,
            "publisher_metadata_sha256": probe.METADATA_SHA, "split": probe.SPLIT,
            "start_batch_index": 3, "records_examined": 4, "next_batch_index": 7, "etag": '"immutable"',
        }
        path = Mock()
        path.open.return_value = io.BytesIO(json.dumps(record).encode())
        start, etag = probe.read_resume(path)
        self.assertEqual(start, 7)
        storage = FakeStorage(b"abcd")
        with probe.RangeFile("https://example.invalid", 4, probe.Budget(), storage, etag=etag) as handle:
            self.assertEqual(handle.read(4), b"abcd")
        self.assertEqual(storage.requests[0].get_header("If-match"), '"immutable"')

        for invalid in ({"split": "daun_v3-validation"}, {"etag": 'W/"immutable"'}, {"next_batch_index": -1}):
            path.open.return_value = io.BytesIO(json.dumps(dict(record, **invalid)).encode())
            with self.assertRaises(probe.ProbeError):
                probe.read_resume(path)

    def test_resume_rejects_cursor_without_matching_progress(self):
        record = {
            "status": "INCOMPLETE", "dataset": probe.DATASET, "version": probe.VERSION,
            "object": probe.SOURCE, "object_bytes": probe.EXPECTED_BYTES, "storage_host": probe.STORAGE_HOST,
            "storage_path": probe.STORAGE_PATH, "frozen_source_archive_sha256": probe.ARCHIVE_SHA,
            "frozen_private_proposal_sha256": probe.PROPOSAL_SHA,
            "publisher_metadata_sha256": probe.METADATA_SHA, "split": probe.SPLIT,
            "start_batch_index": 0, "records_examined": 1, "next_batch_index": 7, "etag": '"immutable"',
        }
        path = Mock()
        path.open.return_value = io.BytesIO(json.dumps(record).encode())
        with self.assertRaisesRegex(probe.ProbeError, "progress"):
            probe.read_resume(path)

    def test_malformed_unselected_anatomy_is_observed_without_blocking_development_match(self):
        examples = {
            b"malformed": example_fixture(selected=False, malformed=True),
            b"development": example_fixture(selected=True),
        }
        module, protobuf = fake_arrow(examples)
        roster = probe.index_roster([clip()])
        with module, protobuf:
            receipt = {}
            output = probe.scan_first(io.BytesIO(b"ARROW1"), roster, receipt)
            self.assertEqual(output["record_sha256"], hashlib.sha256(b"development").hexdigest())
            self.assertEqual(receipt["matched_batch_index"], 1)
            self.assertEqual(receipt["first_decoded_schema"], {
                "status": "UNSUPPORTED", "signer_wire_kind": "bytes_list",
            })
            self.assertNotIn("private-nonroster-target", json.dumps(receipt))
            self.assertNotIn("private-nonroster-target", json.dumps(output))

            examples[b"malformed"].context.feature["prompt"] = feature("bytes_list", (b"private-target",))
            with self.assertRaisesRegex(probe.ProbeError, "invalid anatomy"):
                probe.scan_first(io.BytesIO(b"ARROW1"), roster, {})

    def test_unsupported_unselected_frame_count_does_not_block_development_match(self):
        unsupported = example_fixture(selected=False)
        unsupported.context.feature["num_frames"] = feature("int64_list", (601,))
        examples = {
            b"unsupported": unsupported,
            b"development": example_fixture(selected=True),
        }
        module, protobuf = fake_arrow(examples)
        with module, protobuf:
            with self.assertRaisesRegex(probe.ProbeError, "frame count or image dimensions"):
                probe.scan_first(
                    io.BytesIO(b"ARROW1"), probe.index_roster([dict(clip(), reference="private-nonroster-target")]), {}
                )
            receipt = {}
            output = probe.scan_first(io.BytesIO(b"ARROW1"), probe.index_roster([clip()]), receipt)

        self.assertEqual(output["record_sha256"], hashlib.sha256(b"development").hexdigest())
        self.assertEqual(receipt["records_examined"], 2)
        self.assertEqual(receipt["matched_batch_index"], 1)
        self.assertEqual(receipt["first_decoded_schema"], {
            "status": "UNSUPPORTED", "signer_wire_kind": "bytes_list",
        })

    def test_budget_expiration_while_decoding_unselected_row_stops_scan(self):
        examples = {
            b"unselected": example_fixture(selected=False),
            b"development": example_fixture(selected=True),
        }
        module, protobuf = fake_arrow(examples)
        roster = probe.index_roster([clip()])
        receipt = {}
        decode = probe.decode_landmarks

        def expire_on_unselected(example):
            if example is examples[b"unselected"]:
                raise probe.BudgetExceeded("wall-time budget exhausted")
            return decode(example)

        with module, protobuf, patch.object(probe, "decode_landmarks", side_effect=expire_on_unselected) as decoder:
            with self.assertRaisesRegex(probe.BudgetExceeded, "wall-time budget exhausted"):
                probe.scan_first(io.BytesIO(b"ARROW1"), roster, receipt)

        self.assertEqual(decoder.call_count, 1)
        self.assertNotIn("first_decoded_schema", receipt)
        self.assertNotIn("next_batch_index", receipt)

    def test_budget_expiration_after_match_retries_matched_batch(self):
        examples = {
            b"unselected": example_fixture(selected=False),
            b"development": example_fixture(selected=True),
        }
        module, protobuf = fake_arrow(examples)
        with module, protobuf:
            assert_matched_batch_resumes(self, b"ARROW1", b"development")

    def test_roster_join_requires_every_field_and_unique_development_path(self):
        index = probe.index_roster([clip()])
        key = ("take_1.000_2.000.mp4", "0007", "private-target")
        self.assertEqual(index[key], clip())
        self.assertNotIn((key[0], "7", key[2]), index)
        self.assertNotIn((key[0], key[1], "private-target "), index)
        with self.assertRaisesRegex(probe.ProbeError, "ambiguous"):
            probe.index_roster([clip(), clip()])
        with self.assertRaises(probe.ProbeError):
            probe.index_roster([dict(clip(), split="daun_v3-test")])

    def test_frozen_development_split_is_checked_before_any_storage_request(self):
        development = [
            dict(clip(), reference=f"dev-{index}", signer_id=f"d{index}",
                 full_video_filename=f"d{index}.mp4",
                 source_path=f"daun_v3/video_clips/daun_v3-train/d{index}.mp4")
            for index in range(32)
        ]
        holdout = [
            dict(reference=f"held-{index}", signer_id=f"h{index}", full_video_filename=f"h{index}.mp4",
                 split="daun_v3-validation",
                 source_path=f"daun_v3/video_clips/daun_v3-validation/h{index}.mp4")
            for index in range(64)
        ]
        metadata = json.dumps({
            "id": 5151887, "ref": "googleai/fsboard", "currentVersionNumber": 13,
            "licenseName": "Attribution 4.0 International (CC BY 4.0)",
        }).encode()

        def frozen_package(rows):
            b = json.dumps({
                "dataset": {"id": "googleai/fsboard", "version": 13},
                "splits": {"development": {"publisher_split": "daun_v3-train"}},
                "clips": {"development": rows, "holdout": holdout},
            }).encode()
            sink = io.BytesIO()
            with tarfile.open(fileobj=sink, mode="w:gz") as archive:
                member = tarfile.TarInfo("private-proposal.json")
                member.size = len(b)
                archive.addfile(member, io.BytesIO(b))
            return sink.getvalue(), hashlib.sha256(b).hexdigest()

        package, pin = frozen_package(development)
        with patch.object(probe, "pinned_bytes", side_effect=[metadata, package]), patch.object(probe, "PROPOSAL_SHA", pin):
            self.assertEqual(len(probe.load_roster(None, None)), 32)

        for wrong_split in ("daun_v3-validation", "daun_v3-test", "unknown"):
            mismatched = dict(development[0], split=wrong_split)
            package, pin = frozen_package([mismatched] + development[1:])
            with (
                patch.object(probe, "pinned_bytes", side_effect=[metadata, package]),
                patch.object(probe, "PROPOSAL_SHA", pin),
                patch.object(probe.signal, "setitimer", create=True),
                patch.object(probe.signal, "ITIMER_REAL", 0, create=True),
                patch.object(probe, "resolve_storage") as network,
                patch.object(probe, "write_private_json") as receipt,
            ):
                result = probe.run(None, None, Mock(), probe.Budget())
                self.assertEqual(result, "FAILED")
                network.assert_not_called()
                self.assertIn("publisher split", receipt.call_args.args[2]["reason"])

    def test_preserves_measured_timestamps_coordinates_and_missing_hand(self):
        result = probe.landmark_frames(series(), 3, 29.875)
        self.assertEqual(result["timestamp_us"], [28, 33_500, 66_972])
        self.assertEqual(result["groups"]["left_hand"][0]["x"][0], 0.7)
        self.assertIsNone(result["groups"]["right_hand"][1])
        self.assertEqual(len(result["groups"]["pose"][0]["z"]), 25)
        self.assertEqual(len(result["groups"]["face"][0]["x"]), 478)

    def test_preserves_short_intervals_in_measured_timestamps(self):
        timestamps = [28, 61, 66_972]
        result = probe.landmark_frames({"timestamp": [(value,) for value in timestamps]}, 3, 29.875)
        self.assertEqual(result["timestamp_us"], timestamps)

    def test_preserves_bursty_variable_frame_timing(self):
        timestamps = [0, 5_000, 10_000, 15_000, 20_000, 25_000, 200_000]
        result = probe.landmark_frames({"timestamp": [(value,) for value in timestamps]}, 7, 30)
        self.assertEqual(result["timestamp_us"], timestamps)

    def test_rejects_nonincreasing_or_wrong_unit_timestamps(self):
        for timestamps in (
            [28, 28, 66_972],
            [28, 67_000, 66_972],
            [0, 33, 67],
            [0, 33_472_000, 66_944_000],
        ):
            with self.subTest(timestamps=timestamps), self.assertRaises(probe.ProbeError):
                probe.landmark_frames({"timestamp": [(value,) for value in timestamps]}, 3, 29.875)

    def test_rejects_partial_axes_wrong_anatomy_and_nonfinite_coordinates(self):
        for axis, value in (
            ("x_right_hand", (0.2,) * 21),
            ("x_pose", (0.0,) * 24),
            ("z_left_hand", (float("nan"),) * 21),
        ):
            with self.subTest(axis=axis, count=len(value)):
                values = series()
                values[axis][1] = value
                with self.assertRaises(probe.ProbeError):
                    probe.landmark_frames(values, 3, 29.875)


class CloudWheelIntegration(unittest.TestCase):
    @staticmethod
    def arrow_fixture(*, malformed=False, first_frame_count=3):
        import pyarrow as pa

        sequence_type = probe.sequence_example_type()
        example = sequence_type()
        for field, value in (
            ("video_name", b"take_1.000_2.000.mp4"),
            ("signer_id", b"0007"),
            ("prompt", b"private-target"),
        ):
            example.context.feature[field].bytes_list.value.append(value)
        for field, value in (("num_frames", 3), ("image/width", 640), ("image/height", 480)):
            example.context.feature[field].int64_list.value.append(value)
        example.context.feature["image/frame_rate"].float_list.value.append(29.875)
        for key, frames in series().items():
            for frame in frames:
                feature = example.feature_lists.feature_list[key].feature.add()
                if frame:
                    kind = "int64_list" if key == "timestamp" else "float_list"
                    getattr(feature, kind).value.extend(frame)

        raw = example.SerializeToString()
        example.context.feature["prompt"].bytes_list.value[0] = b"private-nonroster-target"
        example.context.feature["num_frames"].int64_list.value[0] = first_frame_count
        if malformed:
            del example.feature_lists.feature_list["x_pose"].feature[1].float_list.value[-1]
        other = example.SerializeToString()
        table = pa.table({"serialized": pa.array([other, raw], type=pa.binary())})
        sink = pa.BufferOutputStream()
        with pa.ipc.new_file(sink, table.schema, options=pa.ipc.IpcWriteOptions(compression="zstd")) as writer:
            writer.write_table(table, max_chunksize=1)
        return sink.getvalue().to_pybytes(), raw

    def test_zstd_arrow_random_access_and_protobuf_round_trip(self):
        data, raw = self.arrow_fixture()
        storage = FakeStorage(data)
        with probe.RangeFile("https://example.invalid", len(data), probe.Budget(), storage) as handle:
            receipt = {}
            output = probe.scan_first(handle, probe.index_roster([clip()]), receipt)
        self.assertIsNotNone(output)
        self.assertEqual(output["record_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(output["landmarks"]["timestamp_us"], [28, 33500, 66972])
        self.assertEqual(output["annotation_window_full_video_seconds"], [1.0, 2.0])
        self.assertEqual(output["raw_clip_window_full_video_seconds"], [0.8, 2.2])
        self.assertEqual(output["scope"], "ORACLE_ANNOTATION_WINDOW_DIAGNOSTIC_ONLY")
        self.assertNotIn("private-target", json.dumps(output))
        self.assertEqual(receipt["records_examined"], 2)
        self.assertEqual(receipt["next_batch_index"], 1)
        self.assertEqual(receipt["matched_batch_index"], 1)
        self.assertEqual(receipt["observed_signer_wire_kinds"], {"bytes_list": 2})
        self.assertNotIn("private-nonroster-target", json.dumps(receipt))
        self.assertEqual(receipt["matched_group_axis_lengths"]["right_hand"]["x"], [0, 21])

    def test_zstd_malformed_unselected_row_and_interrupted_match_resume(self):
        data, raw = self.arrow_fixture(malformed=True)
        assert_matched_batch_resumes(self, data, raw)

    def test_zstd_unsupported_frame_count_is_rejected_only_when_selected(self):
        data, raw = self.arrow_fixture(first_frame_count=601)
        storage = FakeStorage(data)
        first_roster = probe.index_roster([dict(clip(), reference="private-nonroster-target")])
        with probe.RangeFile("https://example.invalid", len(data), probe.Budget(), storage) as handle:
            with self.assertRaisesRegex(probe.ProbeError, "frame count or image dimensions"):
                probe.scan_first(handle, first_roster, {})
        with probe.RangeFile("https://example.invalid", len(data), probe.Budget(), storage) as handle:
            receipt = {}
            output = probe.scan_first(handle, probe.index_roster([clip()]), receipt)

        self.assertEqual(output["record_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(output["frame_count"], 3)
        self.assertEqual(receipt["matched_batch_index"], 1)
        self.assertEqual(receipt["first_decoded_schema"], {
            "status": "UNSUPPORTED", "signer_wire_kind": "bytes_list",
        })

    def test_signer_scalar_preserves_exact_wire_value(self):
        example = probe.sequence_example_type()()
        feature = example.context.feature["signer_id"]
        feature.bytes_list.value.append(b"0007")
        self.assertEqual(probe.signer_scalar(example.context.feature), ("0007", "bytes_list"))
        feature.int64_list.value.append(7)
        self.assertEqual(probe.signer_scalar(example.context.feature), ("7", "int64_list"))
        feature.int64_list.value.append(8)
        with self.assertRaises(probe.ProbeError):
            probe.signer_scalar(example.context.feature)
        feature.float_list.value.append(7.0)
        with self.assertRaises(probe.ProbeError):
            probe.signer_scalar(example.context.feature)


if __name__ == "__main__":
    unittest.main()

