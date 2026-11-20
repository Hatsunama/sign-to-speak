"""Exercise the production proposal, bounded transfer, and admission publication gates."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import signal
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from PIL import Image

import fsboard_admission as shared
import fsboard_task_admission as admission
import fsboard_task_protocol as protocol
import fsboard_training_intake as intake


def proposal_fixture():
    clips, reports = {}, {}
    index = 0
    for role, split in protocol.ROLE_SOURCES.items():
        quota = protocol.QUOTAS[role]
        candidates = []
        for offset in range(quota.clips):
            index += 1
            suffix = chr(97 + index // 26) + chr(97 + index % 26)
            reference = ("jzz " if offset % 2 else "bad ") + suffix + "x"
            source_bytes = f"fixture-video-{index}".encode()
            row = {
                "signingType": "fs", "sensitiveContent": False, "phrase": reference,
                "signerId": f"private-signer-{role}-{offset % quota.signers}",
                "fullVideoFilename": f"private-record-{index}.mp4",
                "clipFilename": f"private-clip-{index}.mp4",
                "clipFileSize": len(source_bytes),
                "clipFileMd5": hashlib.md5(source_bytes, usedforsecurity=False).hexdigest(),
                "clipStartTimeS": 10, "clipEndTimeS": 12,
                "annotationStartTimeS": 10.2, "annotationEndTimeS": 11.8,
            }
            clip = shared.validated_fingerspelling_clip(
                row, video_dir=protocol.video_directory(split), target_pattern=protocol.TARGET_PATTERN,
            )
            candidates.append({**clip, "split": f"daun_v3-{split}"})
        clips[role], reports[role] = intake.select_role(candidates, quota, set())
        reports[role]["publisher_split"] = f"daun_v3-{split}"
    return {
        "schema_version": 1,
        "status": "metadata_proposal",
        "protocol": protocol.frozen_protocol(),
        "dataset": {
            "id": intake.DATASET, "version": intake.VERSION, "license": "CC-BY-4.0",
            "source_pins": {
                name: {"path": name, "bytes": size, "sha256": digest}
                for name, (size, digest) in intake.METADATA_PINS.items()
            },
        },
        "publisher_signer_and_recording_separation_verified": True,
        "previous_evaluation_excluded_clips": 8,
        "contains_private_signer_ids_recordings_and_targets": True,
        "source_stats": {"test": {"excluded_from_all_experiment_roles": True}},
        "scope": {
            "media_downloaded_bytes": 0,
            "source_video_verified": False,
            "frame_sampling_verified": False,
            "training_started": False,
            "model_scoring_started": False,
        },
        "splits": reports,
        "clips": clips,
    }


class Response(io.BytesIO):
    status = 200

    def __init__(self, data, headers):
        super().__init__(data)
        self.headers = headers
        self.read_limits = []

    def read(self, size):
        self.read_limits.append(size)
        return super().read(size)


def decode_fixture(video, output, clip_id, clip, *, count=32, **bounds):
    assert video.read_bytes().startswith(b"fixture-video-")
    folder = output / "frames" / clip_id
    folder.mkdir()
    samples = []
    start, end = shared.annotation_window(clip)
    for slot in range(count):
        path = folder / f"{slot:02d}.png"
        Image.new("RGB", (4, 3), (slot, 0, 0)).save(path, format="PNG")
        payload = path.read_bytes()
        actual = start + (end - start) * slot / (count - 1)
        samples.append({
            "frame_index": slot,
            "requested_seconds": actual,
            "actual_seconds": actual,
            "source_pts_seconds": actual,
            "annotation_offset_seconds": actual - start,
            "duplicate": False,
            "path": path.relative_to(output).as_posix(),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "width": 4,
            "height": 3,
        })
    return {"frames": samples}


class TaskAdmissionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.output = self.directory / "private"
        self.proposal = self.directory / "private-proposal.json"
        self.previous_path = self.directory / "prior.json"
        self.document = proposal_fixture()
        self.previous = [
            {field: f"prior-{field}-{index}" for field in intake.SEPARATION_FIELDS}
            for index in range(8)
        ]
        self.read_previous = self.enterContext(patch.object(intake, "read_previous", return_value=self.previous))
        self.opener = self.enterContext(patch.object(admission, "build_opener"))
        self.open = self.opener.return_value.open
        self.responses = []
        self.open.side_effect = self.response
        self.decode = self.enterContext(patch.object(admission, "decode_frames", side_effect=decode_fixture))

    def response(self, request, timeout):
        body = json.loads(request.data)
        index = Path(body["fileName"]).stem.rsplit("-", 1)[-1]
        data = f"fixture-video-{index}".encode()
        response = Response(data, {"Content-Length": str(len(data))})
        self.responses.append(response)
        return response

    def arguments(self, mode="sentinel"):
        payload = json.dumps(self.document).encode()
        self.proposal.write_bytes(payload)
        return {
            "proposal": self.proposal,
            "proposal_sha256": hashlib.sha256(payload).hexdigest(),
            "protocol_sha256": admission.canonical_sha256(protocol.frozen_protocol()),
            "previous_receipt": self.previous_path,
            "output": self.output,
            "mode": mode,
        }

    def test_sentinel_requests_first_clip_per_role_and_publishes_bound_receipts(self):
        args = self.arguments()
        counts = {"holdout": [29], "development": [21], "training": [8]}

        def variable_decode(*args, **kwargs):
            role = args[2].rsplit("-", 1)[0]
            return decode_fixture(*args, count=counts[role][0], **kwargs)

        self.decode.side_effect = variable_decode
        summary = admission.admit(**args)
        self.read_previous.assert_called_once_with(self.previous_path)
        self.assertEqual(self.open.call_count, 3)
        self.assertEqual(self.decode.call_count, 3)
        self.assertFalse(summary["training_media_complete"])
        self.assertEqual(summary["status"], "sentinel_verified")
        self.assertEqual(summary["proposal_sha256"], args["proposal_sha256"])
        self.assertEqual(summary["protocol_sha256"], args["protocol_sha256"])
        receipt = json.loads((self.output / "admission.json").read_bytes())
        self.assertEqual(set(receipt["clips"]), set(protocol.ROLE_SOURCES))
        for header in (summary, receipt):
            self.assertEqual(header["min_frames_per_clip"], 8)
            self.assertEqual(header["max_frames_per_clip"], 32)
            self.assertEqual(header["sampled_frame_counts"], counts)
            self.assertNotIn("frames_per_clip", header)
        for index, role in enumerate(protocol.ROLE_SOURCES):
            with self.subTest(role=role):
                expected = self.document["clips"][role][0]
                request = self.open.call_args_list[index].args[0]
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(request.full_url, shared.ENDPOINT)
                self.assertEqual(json.loads(request.data), {
                    "ownerSlug": "googleai", "datasetSlug": "fsboard", "datasetVersionNumber": 13,
                    "fileName": expected["source_path"],
                })
                self.assertEqual(self.responses[index].read_limits, [expected["original_bytes"] + 1, 1])
                admitted, = receipt["clips"][role]
                self.assertEqual(admitted["metadata_md5"], expected["metadata_md5"])
                self.assertEqual(admitted["role"], role)
                original = self.output / admitted["original_path"]
                self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), admitted["original_sha256"])
                self.assertEqual(self.decode.call_args_list[index].kwargs, {
                    "min_frames": 8, "max_frames": 32, "allow_terminal_pts_endpoint": True,
                })
                self.assertEqual(admitted["sampled_frame_count"], counts[role][0])
                self.assertEqual(len(admitted["frames"]), counts[role][0])
                self.assertEqual([Path(frame["path"]).name for frame in admitted["frames"]],
                                 [f"{slot:02d}.png" for slot in range(counts[role][0])])
        public_bytes = (self.output / "summary.json").read_bytes()
        self.assertNotIn(b"private-clip", public_bytes)
        self.assertNotIn(b"private-signer", public_bytes)
        self.assertEqual(summary["admission_sha256"], hashlib.sha256(
            (self.output / "admission.json").read_bytes()).hexdigest())
        self.assertEqual(sorted(path.name for path in self.directory.iterdir()), ["private", "private-proposal.json"])

    def test_full_mode_attempts_second_frozen_clip_in_order_and_stops_at_first_failure(self):
        def fail_second(request, timeout):
            if self.responses:
                raise URLError("https://private.test/signed?video=private-clip")
            return self.response(request, timeout)

        self.open.side_effect = fail_second
        args = self.arguments("full")
        argv = [item for key, value in args.items()
                for item in ("--" + key.replace("_", "-"), str(value))]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(admission.main(argv), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn("private", stderr.getvalue())
        self.assertEqual(self.decode.call_count, 1)
        self.assertEqual(self.open.call_count, 2)
        second = json.loads(self.open.call_args.args[0].data)["fileName"]
        self.assertEqual(second, self.document["clips"]["holdout"][1]["source_path"])
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.directory.glob(".fsboard-media-*")))
        failure = json.loads((self.directory / "private.failed.json").read_bytes())
        self.assertEqual(failure["status"], "failed")
        self.assertEqual(failure["stage"], "download")
        self.assertEqual(failure["clips_verified"]["holdout"], 1)
        self.assertEqual(failure["role"], "holdout")
        self.assertNotIn("signed?", json.dumps(failure))

    def assert_cli_publication_survives(self):
        args = self.arguments()
        argv = [item for key, value in args.items()
                for item in ("--" + key.replace("_", "-"), str(value))]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = admission.main(argv)
        summary = json.loads((self.output / "summary.json").read_bytes())
        receipt = (self.output / "admission.json").read_bytes()
        self.assertEqual(summary["admission_sha256"], hashlib.sha256(receipt).hexdigest())
        self.assertEqual(summary["status"], "sentinel_verified")
        self.assertFalse(admission.failure_path(self.output).exists())
        self.assertFalse(list(self.directory.glob(".fsboard-*")))
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue()), summary)

    def test_sigterm_after_actual_rename_preserves_published_admission(self):
        real_rename = Path.rename

        def rename_then_cancel(staging, output):
            real_rename(staging, output)
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

        with patch.object(Path, "rename", rename_then_cancel):
            self.assert_cli_publication_survives()

    def test_sigterm_during_temporary_directory_exit_preserves_published_admission(self):
        real_exit = tempfile.TemporaryDirectory.__exit__
        exited = []

        def exit_then_cancel(temporary, *exc):
            real_exit(temporary, *exc)
            if Path(temporary.name).name.startswith(".fsboard-media-"):
                exited.append(temporary.name)
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

        with patch.object(tempfile.TemporaryDirectory, "__exit__", exit_then_cancel):
            self.assert_cli_publication_survives()
        self.assertEqual(len(exited), 1)

    def test_cancellation_before_rename_records_failure_and_exits_nonzero(self):
        args = self.arguments()
        argv = [item for key, value in args.items()
                for item in ("--" + key.replace("_", "-"), str(value))]
        with (
            patch.object(Path, "rename", side_effect=KeyboardInterrupt),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = admission.main(argv)
        self.assertEqual(code, 130)
        self.assertEqual(stdout.getvalue(), "")
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.directory.glob(".fsboard-*")))
        failure = json.loads(admission.failure_path(self.output).read_bytes())
        self.assertEqual((failure["status"], failure["stage"]), ("cancelled", "publication"))
        self.assertEqual(failure["clips_verified"], dict.fromkeys(protocol.ROLE_SOURCES, 1))

    def test_rename_failure_does_not_adopt_a_different_output_directory(self):
        publication_error = PermissionError("rename denied")

        def competing_publication(staging, output):
            shutil.copytree(staging, output)
            raise publication_error

        with patch.object(Path, "rename", competing_publication):
            with self.assertRaises(PermissionError) as raised:
                admission.admit(**self.arguments())
        self.assertIs(raised.exception, publication_error)
        self.assertTrue((self.output / "summary.json").is_file())
        self.assertTrue((self.output / "admission.json").is_file())
        self.assertFalse(list(self.directory.glob(".fsboard-*")))
        failure = json.loads(admission.failure_path(self.output).read_bytes())
        self.assertEqual((failure["status"], failure["stage"]), ("failed", "publication"))

    def test_failure_receipt_link_error_does_not_hide_publication_error(self):
        publication_error = PermissionError("rename denied")
        with (
            patch.object(Path, "rename", side_effect=publication_error),
            patch.object(Path, "hardlink_to", side_effect=KeyboardInterrupt),
            self.assertRaises(PermissionError) as raised,
        ):
            admission.admit(**self.arguments())
        self.assertIs(raised.exception, publication_error)
        self.assertFalse(self.output.exists())
        self.assertFalse(admission.failure_path(self.output).exists())
        self.assertFalse(list(self.directory.glob(".fsboard-*")))

    def test_existing_output_is_preserved_without_any_requests(self):
        self.output.mkdir()
        marker = self.output / "existing"
        marker.write_bytes(b"original")
        with self.assertRaises(shared.AdmissionError):
            admission.admit(**self.arguments())
        self.assertEqual(marker.read_bytes(), b"original")
        self.open.assert_not_called()

    def test_existing_failure_receipt_is_preserved_without_any_requests(self):
        receipt = self.directory / "private.failed.json"
        original = b'{"status":"failed","stage":"download"}'
        receipt.write_bytes(original)

        with self.assertRaisesRegex(shared.AdmissionError, "already exists"):
            admission.admit(**self.arguments())

        self.assertEqual(receipt.read_bytes(), original)
        self.assertFalse(self.output.exists())
        self.read_previous.assert_not_called()
        self.open.assert_not_called()

    def test_retry_cannot_replace_failed_receipt_and_fresh_output_succeeds(self):
        first_error = URLError("first transfer failed")
        self.open.side_effect = first_error
        args = self.arguments()
        with self.assertRaises(URLError) as raised:
            admission.admit(**args)
        self.assertIs(raised.exception, first_error)
        receipt = self.directory / "private.failed.json"
        original = receipt.read_bytes()
        self.assertEqual(json.loads(original)["status"], "failed")

        self.open.side_effect = self.response
        with self.assertRaisesRegex(shared.AdmissionError, "already exists"):
            admission.admit(**args)
        self.open.assert_called_once()
        self.assertFalse(self.output.exists())

        self.output = self.directory / "fresh-attempt"
        self.assertEqual(admission.admit(**self.arguments())["status"], "sentinel_verified")
        self.assertEqual(receipt.read_bytes(), original)

    def test_failure_publication_preserves_receipt_created_during_attempt(self):
        receipt = self.directory / "private.failed.json"
        original = b'{"status":"cancelled","stage":"decode"}'
        first_error = URLError("transfer failed")
        self.open.side_effect = first_error

        def concurrent_receipt(path, value):
            shared.write_json(path, value)
            receipt.write_bytes(original)

        with patch.object(admission, "write_json", side_effect=concurrent_receipt):
            with self.assertRaises(URLError) as raised:
                admission.admit(**self.arguments())
        self.assertIs(raised.exception, first_error)
        self.assertEqual(receipt.read_bytes(), original)
        self.assertFalse(list(self.directory.glob(".fsboard-*")))

    def test_failure_write_problem_preserves_original_error(self):
        for diagnostic_error in (OSError("diagnostic unavailable"), KeyboardInterrupt()):
            with self.subTest(diagnostic_error=diagnostic_error):
                first_error = URLError("transfer failed")
                self.open.side_effect = first_error
                with patch.object(admission, "write_json", side_effect=diagnostic_error):
                    with self.assertRaises((URLError, KeyboardInterrupt)) as raised:
                        admission.admit(**self.arguments())
                self.assertIs(raised.exception, first_error)
                self.assertFalse(list(self.directory.glob(".fsboard-*")))

    def test_digest_mismatch_fails_before_network(self):
        for field in ("proposal_sha256", "protocol_sha256"):
            with self.subTest(field=field):
                self.output = self.directory / field
                args = self.arguments()
                args[field] = "0" * 64
                with self.assertRaises(shared.AdmissionError):
                    admission.admit(**args)
        self.open.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_clip_validation_accepts_the_shared_metadata_duration_range(self):
        for role, split in protocol.ROLE_SOURCES.items():
            clip = self.document["clips"][role][0]
            start = clip["annotation_start_full_video_seconds"]
            for duration in (0.5, 0.7, 0.917, 1.199, 1.2):
                with self.subTest(role=role, duration=duration):
                    candidate = {**clip, "annotation_end_full_video_seconds": start + duration}
                    admission.validate_clip(candidate, split)
            too_short = {**clip, "annotation_end_full_video_seconds": start + 0.499}
            with self.assertRaises(shared.AdmissionError):
                admission.validate_clip(too_short, split)

    def test_semantic_gates_check_full_roster_before_sentinel_download(self):
        cases = {
            "publisher test path": ("source_path", "daun_v3/video_clips/daun_v3-test/other.mp4"),
            "traversal basename": ("clip_filename", "../other.mp4"),
            "oversized file": ("original_bytes", shared.CLIP_BYTES + 1),
            "invalid MD5": ("metadata_md5", "0" * 31),
            "invalid literal target": ("reference", "uppercase I"),
            "extra annotation label": ("signingType", "asl"),
            "invalid timestamp": ("annotation_end_full_video_seconds", 20),
        }
        for name, (field, value) in cases.items():
            with self.subTest(name=name):
                self.output = self.directory / field
                self.document = proposal_fixture()
                self.document["clips"]["training"][-1][field] = value
                with self.assertRaises(shared.AdmissionError):
                    admission.admit(**self.arguments())
        self.open.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_all_prior_and_cross_role_fields_are_checked(self):
        for field in intake.SEPARATION_FIELDS:
            with self.subTest(field=field):
                self.output = self.directory / field
                self.previous[0][field] = self.document["clips"]["development"][-1][field].upper()
                with self.assertRaises(shared.AdmissionError):
                    admission.admit(**self.arguments())
                self.previous[0][field] = f"prior-{field}-0"
        self.output = self.directory / "cross-role"
        train = self.document["clips"]["training"][-1]
        train["signer_id"] = self.document["clips"]["development"][-1]["signer_id"].upper()
        with self.assertRaises(shared.AdmissionError):
            admission.admit(**self.arguments())
        self.open.assert_not_called()

    def test_declared_shortfalls_and_absent_quota_rows_reject(self):
        self.document["splits"]["holdout"]["shortfalls"]["ordinary"] = 1
        with self.assertRaises(shared.AdmissionError):
            admission.admit(**self.arguments())
        self.output = self.directory / "absent-quota"
        self.document["splits"]["holdout"]["shortfalls"]["ordinary"] = 0
        self.document["clips"]["training"].pop()
        with self.assertRaises(shared.AdmissionError):
            admission.admit(**self.arguments())
        self.open.assert_not_called()

    def test_content_length_and_md5_bound_stream_fail_before_decode(self):
        clip = self.document["clips"]["holdout"][0]
        size = clip["original_bytes"]
        responses = [
            (b"a" * (size + 1), {}),
            (b"a" * (size - 1), {}),
            (b"a" * size, {"Content-Length": str(size - 1)}),
            (b"a" * size, {"Content-Length": str(size)}),
        ]
        for index, (data, headers) in enumerate(responses):
            with self.subTest(length=len(data), headers=headers):
                self.output = self.directory / f"transfer-{index}"
                response = Response(data, headers)
                self.open.side_effect = lambda *args, **kwargs: response
                with self.assertRaises(shared.AdmissionError):
                    admission.admit(**self.arguments())
                self.assertTrue(all(0 < limit <= size + 1 for limit in response.read_limits))
                self.assertFalse(self.output.exists())
                self.assertFalse(list(self.directory.glob(".fsboard-media-*")))
        self.decode.assert_not_called()

    def test_out_of_range_frame_counts_fail_before_publication(self):
        for count in (7, 33):
            with self.subTest(count=count):
                self.output = self.directory / f"count-{count}"

                def out_of_range(*args, **kwargs):
                    return decode_fixture(*args, count=count, **kwargs)

                self.decode.side_effect = out_of_range
                with self.assertRaises(shared.AdmissionError):
                    admission.admit(**self.arguments())
                self.assertFalse(self.output.exists())
                failure = json.loads(admission.failure_path(self.output).read_text(encoding="utf-8"))
                self.assertEqual(failure["stage"], "frame_verification")

    def test_padding_and_repeated_presentation_timestamps_fail_before_publication(self):
        for field in ("duplicate", "source_pts_seconds", "frame_index"):
            with self.subTest(field=field):
                self.output = self.directory / field

                def invalid_frame(*args, **kwargs):
                    result = decode_fixture(*args, count=21, **kwargs)
                    first, second = result["frames"][:2]
                    second[field] = True if field == "duplicate" else first[field]
                    return result

                self.decode.side_effect = invalid_frame
                with self.assertRaisesRegex(shared.AdmissionError, "out of order or out of scope"):
                    admission.admit(**self.arguments())
                self.assertFalse(self.output.exists())

    def test_receipt_requires_complete_ordered_frame_artifacts(self):
        def remove_frame(*args, **kwargs):
            result = decode_fixture(*args, count=21, **kwargs)
            (args[1] / result["frames"][-1]["path"]).unlink()
            return result

        self.decode.side_effect = remove_frame
        with self.assertRaises((shared.AdmissionError, FileNotFoundError)):
            admission.admit(**self.arguments())
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.directory.glob(".fsboard-media-*")))

    def test_png_content_and_dimensions_are_verified_even_when_the_digest_matches(self):
        def invalid_frame(*args, **kwargs):
            result = decode_fixture(*args, **kwargs)
            sample = result["frames"][0]
            path = args[1] / sample["path"]
            if defect == "dimensions":
                sample["height"] += 1
            elif defect == "mode":
                Image.new("L", (4, 3)).save(path, format="PNG")
            else:
                payload = path.read_bytes()
                idat = payload.index(b"IDAT")
                if defect == "truncated":
                    payload = payload[:idat + 6]
                elif defect == "missing-end":
                    payload = payload[:-12]  # Remove the fixture's complete IEND chunk.
                else:
                    length = int.from_bytes(payload[idat - 4:idat], "big")
                    crc = idat + 4 + length
                    payload = payload[:crc] + bytes([payload[crc] ^ 1]) + payload[crc + 1:]
                path.write_bytes(payload)
            sample["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            return result

        for defect in ("dimensions", "mode", "truncated", "missing-end", "bad-crc"):
            with self.subTest(defect=defect):
                self.output = self.directory / defect
                self.decode.side_effect = invalid_frame
                with self.assertRaises(shared.AdmissionError):
                    admission.admit(**self.arguments())
                self.assertFalse(self.output.exists())
                failure = json.loads((self.directory / f"{defect}.failed.json").read_bytes())
                self.assertEqual(failure["stage"], "frame_verification")

    def test_interrupt_discards_stage_and_records_cancellation(self):
        self.decode.side_effect = KeyboardInterrupt
        args = self.arguments()
        with self.assertRaises(KeyboardInterrupt):
            admission.admit(**args)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.directory.glob(".fsboard-media-*")))
        receipt = self.directory / "private.failed.json"
        original = receipt.read_bytes()
        failure = json.loads(original)
        self.assertEqual((failure["status"], failure["stage"]), ("cancelled", "decode"))

        with self.assertRaisesRegex(shared.AdmissionError, "already exists"):
            admission.admit(**args)
        self.open.assert_called_once()
        self.assertEqual(receipt.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()