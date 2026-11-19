"""Exercise the paired trainer's capture contract with small synthetic files."""

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import fsboard_v4_pair_capture as capture
import paired_experiment as paired


class PairCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "local"
        self.destination = self.root / "bucket"
        self.destination.mkdir()
        self.copied = {}

    def write(self, path, data):
        target = self.source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def receipt(self, path, **document):
        return self.write(path, (json.dumps(document) + "\n").encode())

    def adapter(self, path, step=None):
        self.write(f"{path}/adapter_config.json", b'{"r": 8}')
        self.write(f"{path}/adapter_model.safetensors", f"synthetic adapter at {path}".encode())
        self.write(f"{path}/README.md", b"synthetic model card")
        receipt = {"adapter_sha256": paired.adapter_digest(self.source / path),
                   "parameter_sha256": sha256(path.encode()).hexdigest()}
        if step is not None:
            optimizer = b"synthetic optimizer"
            self.write(f"{path}/optimizer.pt", optimizer)
            receipt.update(optimizer_steps=step, optimizer_sha256=sha256(optimizer).hexdigest())
        self.receipt(f"{path}/receipt.json", **receipt)
        return receipt

    def poll(self, strict=False):
        capture.capture_completed(self.source, self.destination, self.copied, strict=strict)

    def completed_pair(self):
        experiment = {
            "schema_version": 1, "experiment_id": "synthetic-pair",
            "model": {"id": "synthetic-base", "revision": "synthetic-revision"}, "pins": {},
        }
        self.receipt("experiment.json", **experiment, source_sha256={},
                     frozen_training_protocol={"optimizer_steps_per_arm": 192})
        self.receipt("runtime_receipt.json", python="3.11.5", packages={}, device="NVIDIA L40S",
                     total_gpu_bytes=48_000_000_000, torch_cuda="12.6", dtype="bfloat16",
                     attention_implementation="sdpa")
        initialization = self.adapter("initialization")
        schedule = {}
        summaries = {}
        for arm in ("A", "B"):
            self.receipt(f"{arm}/initialization.json",
                         shared_adapter_sha256=initialization["adapter_sha256"],
                         trainable_parameter_sha256=initialization["parameter_sha256"])
            schedule[arm] = []
            for step in range(1, 193):
                item = {
                    "video_ids": [f"clip-{2 * step - 1}", f"clip-{2 * step}"],
                    "static_id": f"static-{step}" if arm == "B" else None,
                }
                schedule[arm].append(item)
                self.receipt(f"{arm}/steps/{step:03d}.json", optimizer_steps=step, **item,
                             video_microbatch_losses=[0.5, 0.5], video_loss=0.5,
                             static_loss=0.25 if arm == "B" else None,
                             optimizer_step_seconds=0.1, static_forward_backward_seconds=0.02 if arm == "B" else 0.0,
                             objective=0.55 if arm == "B" else 0.5,
                             gradient_norm_before_clip=0.5, gradient_clip_factor=1.0)
            checkpoint = f"{arm}/checkpoint-192"
            final = self.adapter(checkpoint, step=192)
            summaries[arm] = {
                "optimizer_steps": 192, "video_microbatches": 384,
                "static_microbatches": 192 if arm == "B" else 0,
                "clipped_steps": 0, "optimizer_step_seconds": 19.2,
                "static_forward_backward_seconds": 3.84 if arm == "B" else 0.0,
                "train_wall_seconds": 20.0, "parameter_sha256": final["parameter_sha256"],
                "adapter_sha256": final["adapter_sha256"], "final_checkpoint": checkpoint,
                "final_checkpoint_receipt_sha256": sha256((self.source / checkpoint / "receipt.json").read_bytes()).hexdigest(),
                "peak_gpu_allocated_bytes": 1024, "peak_gpu_reserved_bytes": 2048,
            }
            self.receipt(f"{arm}/summary.json", **summaries[arm])
        schedule_file = self.receipt("schedule.json", **schedule)
        success = {
            **experiment, "status": "completed_training_pair", "arms": summaries,
            "schedule_sha256": sha256(schedule_file.read_bytes()).hexdigest(),
            "initialization_sha256": initialization["adapter_sha256"],
            "processor_token_contract_verified": True, "training_started": True,
            "model_scoring_started": False, "elapsed_seconds": 45.0,
        }
        self.receipt("success.json", **success)
        return success

    def test_captures_only_completed_allowlisted_files_and_publishes_success_on_final_pass(self):
        self.poll()  # The child creates its output directory after supervision starts.
        self.completed_pair()
        self.adapter("A/checkpoint-010", step=10)
        ignored = {"A/raw-video.mp4", "initialization/model.safetensors", "B/checkpoint-192/base.safetensors"}
        for path in ignored:
            self.write(path, b"outside capture scope")

        self.poll()
        self.assertFalse((self.destination / "success.json").exists())
        self.assertEqual(list(self.copied).index(Path("A/checkpoint-010/receipt.json")),
                         list(self.copied).index(Path("A/checkpoint-010/README.md")) + 1)
        self.poll(strict=True)

        expected = {path.relative_to(self.source).as_posix()
                    for path in self.source.rglob("*") if path.is_file()} - ignored
        actual = {path.relative_to(self.destination).as_posix()
                  for path in self.destination.rglob("*") if path.is_file()}
        self.assertEqual(actual, expected)
        for path in expected:
            self.assertEqual((self.destination / path).read_bytes(), (self.source / path).read_bytes())

    def test_terminal_success_with_absent_final_checkpoint_is_rejected(self):
        self.completed_pair()
        checkpoint = self.source / "B/checkpoint-192"
        checkpoint.rename(self.root / checkpoint.name)
        with self.assertRaises(capture.CaptureError):
            self.poll(strict=True)
        self.assertFalse((self.destination / "success.json").exists())
        self.assertTrue((self.destination / "A/checkpoint-192/receipt.json").is_file())

    def test_terminal_success_requires_both_final_checkpoints_and_every_step(self):
        self.completed_pair()
        self.adapter("A/checkpoint-010", step=10)
        paths = ("A/checkpoint-192/adapter_config.json", "A/checkpoint-192/adapter_model.safetensors",
                 "B/checkpoint-192/optimizer.pt", "B/checkpoint-192/receipt.json", "B/steps/191.json")
        for missing in paths:
            with self.subTest(missing=missing):
                original = (self.source / missing).read_bytes()
                (self.source / missing).unlink()
                destination = self.root / missing.replace("/", "_")
                destination.mkdir()
                try:
                    with self.assertRaises(capture.CaptureError):
                        capture.capture_completed(self.source, destination, {}, strict=True)
                    self.assertFalse((destination / "success.json").exists())
                    self.assertTrue((destination / "A/checkpoint-010/receipt.json").is_file())
                finally:
                    self.write(missing, original)

    def test_terminal_success_requires_shared_records(self):
        self.completed_pair()
        paths = ("experiment.json", "runtime_receipt.json", "schedule.json",
                 "initialization/receipt.json", "initialization/adapter_config.json",
                 "initialization/adapter_model.safetensors", "A/initialization.json", "B/summary.json")
        for missing in paths:
            with self.subTest(missing=missing):
                original = (self.source / missing).read_bytes()
                (self.source / missing).unlink()
                destination = self.root / missing.replace("/", "_")
                destination.mkdir()
                try:
                    with self.assertRaises(capture.CaptureError):
                        capture.capture_completed(self.source, destination, {}, strict=True)
                    self.assertFalse((destination / "success.json").exists())
                finally:
                    self.write(missing, original)

    def test_terminal_receipt_cannot_contradict_captured_records(self):
        success = self.completed_pair()
        arm = success["arms"]["B"]
        tampering = (
            (arm, "train_wall_seconds", 99.0, None),
            (success, "status", "cancelled", None),
            (success, "schedule_sha256", "0" * 64, None),
            (success, "initialization_sha256", "0" * 64, None),
            (arm, "optimizer_steps", 191, "B/summary.json"),
            (arm, "final_checkpoint", "A/checkpoint-192", "B/summary.json"),
            (arm, "final_checkpoint_receipt_sha256", "0" * 64, "B/summary.json"),
            (arm, "adapter_sha256", "0" * 64, "B/summary.json"),
        )
        for record, key, value, summary in tampering:
            with self.subTest(key=key):
                original = record[key]
                record[key] = value
                self.receipt("success.json", **success)
                if summary:
                    self.receipt(summary, **record)
                destination = self.root / key
                destination.mkdir()
                try:
                    with self.assertRaises(capture.CaptureError):
                        capture.capture_completed(self.source, destination, {}, strict=True)
                    self.assertFalse((destination / "success.json").exists())
                    self.assertTrue((destination / "A/checkpoint-192/receipt.json").is_file())
                    self.assertTrue((destination / "B/checkpoint-192/receipt.json").is_file())
                finally:
                    record[key] = original
                    if summary:
                        self.receipt(summary, **record)

    def test_invalid_final_checkpoint_preserves_other_completed_checkpoint_and_failure(self):
        self.completed_pair()
        self.adapter("B/checkpoint-010", step=10)
        self.write("A/checkpoint-192/adapter_model.safetensors", b"tampered adapter")
        self.receipt("failure.json", status="cancelled")
        with self.assertRaises(capture.CaptureError):
            self.poll(strict=True)
        for path in ("failure.json", "B/checkpoint-010/receipt.json", "B/checkpoint-192/receipt.json"):
            self.assertTrue((self.destination / path).is_file(), path)
        self.assertFalse((self.destination / "A/checkpoint-192/receipt.json").exists())
        self.assertFalse((self.destination / "success.json").exists())

    def test_copy_error_keeps_original_exception_and_reports_failed_cleanup(self):
        self.receipt("experiment.json", schema_version=1)
        write_error = OSError("original write error")
        cleanup_error = OSError("cleanup unlink error")
        original_open = Path.open

        def failing_open(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            if path == self.destination / "experiment.json":
                return FailingWrite(stream)
            return stream

        class FailingWrite:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.stream.close()

            def write(self, data):
                raise write_error

        with patch.object(Path, "open", failing_open), patch.object(Path, "unlink", side_effect=cleanup_error):
            with self.assertRaises(OSError) as error:
                self.poll()
        self.assertIs(error.exception, write_error)
        notes = "\n".join(getattr(error.exception, "__notes__", []))
        self.assertIn(str(cleanup_error), notes)
        self.assertIn(str(self.destination / "experiment.json"), notes)
        self.assertFalse(self.copied)

    def test_incomplete_json_and_checkpoint_are_skipped_then_rejected_at_final_capture(self):
        step = self.write("A/steps/010.json", b'{"optimizer_steps":10}')
        self.write("A/checkpoint-010/adapter_model.safetensors", b"partial")
        self.write("A/checkpoint-010/receipt.json", b'{"optimizer_steps":')
        self.poll()
        self.assertFalse(self.copied)
        with self.assertRaises(capture.CaptureError):
            self.poll(strict=True)
        step.write_bytes(b'{"optimizer_steps":10}\n')
        self.adapter("A/checkpoint-010", step=10)
        self.poll(strict=True)
        self.assertIn(Path("A/checkpoint-010/receipt.json"), self.copied)

    def test_rejects_wrong_step_and_tampered_optimizer_before_copying_checkpoint(self):
        self.adapter("A/checkpoint-010", step=20)
        with self.assertRaisesRegex(capture.CaptureError, "step"):
            self.poll()
        self.adapter("A/checkpoint-010", step=10)
        self.write("A/checkpoint-010/optimizer.pt", b"changed optimizer")
        with self.assertRaisesRegex(capture.CaptureError, "optimizer"):
            self.poll()
        self.assertFalse((self.destination / "A/checkpoint-010/receipt.json").exists())
        self.assertFalse((self.destination / "A/checkpoint-010/adapter_model.safetensors").exists())

    def test_rejects_tampered_initial_adapter_before_copying(self):
        self.adapter("initialization")
        self.write("initialization/adapter_model.safetensors", b"changed adapter")
        with self.assertRaisesRegex(capture.CaptureError, "adapter"):
            self.poll()
        self.assertFalse((self.destination / "initialization/receipt.json").exists())

    def test_exclusive_creation_preserves_existing_destination_file(self):
        self.receipt("experiment.json", schema_version=1)
        existing = self.destination / "experiment.json"
        existing.write_bytes(b"preexisting")
        with self.assertRaises(capture.CaptureError):
            self.poll()
        self.assertEqual(existing.read_bytes(), b"preexisting")
        self.assertFalse(self.copied)

    def test_rejects_symlink_in_source_and_destination(self):
        self.source.mkdir()
        target = self.root / "outside"
        target.mkdir()
        source_link = self.source / "A"
        def link_directory(link):
            if sys.platform == "win32":
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "$ErrorActionPreference = 'Stop'; "
                     "New-Item -ItemType Junction -Path $env:PAIR_TEST_LINK -Target $env:PAIR_TEST_TARGET "
                     "| Out-Null"],
                    env={**os.environ, "PAIR_TEST_LINK": str(link), "PAIR_TEST_TARGET": str(target)},
                    check=True, capture_output=True,
                )
            else:
                link.symlink_to(target, target_is_directory=True)

        link_directory(source_link)
        with self.assertRaises(capture.CaptureError):
            self.poll()
        if sys.platform == "win32":
            source_link.rmdir()
        else:
            source_link.unlink()
        self.receipt("A/steps/010.json", optimizer_steps=10)
        link_directory(self.destination / "A")
        with self.assertRaises(capture.CaptureError):
            self.poll()
        self.assertFalse(list(target.iterdir()))

    def test_live_poll_skips_previously_saved_bytes_and_final_verifies_both_sides(self):
        self.adapter("A/checkpoint-010", step=10)
        self.poll()
        relative = Path("A/checkpoint-010/optimizer.pt")
        source = self.source / relative
        saved = self.destination / relative
        original = source.read_bytes()
        source.write_bytes(b"x" * len(original))
        self.poll()
        with self.assertRaises(capture.CaptureError):
            self.poll(strict=True)
        source.write_bytes(original)
        saved.write_bytes(b"x" * len(original))
        with self.assertRaises(capture.CaptureError):
            self.poll(strict=True)

    def test_preserves_checkpoint_and_cancellation_receipt_with_incomplete_later_checkpoint(self):
        self.adapter("A/checkpoint-010", step=10)
        self.poll()
        self.adapter("A/checkpoint-020", step=20)
        self.write("A/checkpoint-030/adapter_model.safetensors", b"partial")
        self.receipt("failure.json", status="cancelled")
        with self.assertRaises(capture.CaptureError):
            self.poll(strict=True)
        for path in ("A/checkpoint-010/receipt.json", "A/checkpoint-020/receipt.json", "failure.json"):
            self.assertEqual((self.source / path).read_bytes(), (self.destination / path).read_bytes())
        self.assertFalse((self.destination / "A/checkpoint-030").exists())

    def test_applies_total_capture_budget(self):
        self.adapter("A/checkpoint-010", step=10)
        with patch.object(capture, "MAX_TOTAL_BYTES", 12):
            with self.assertRaisesRegex(capture.CaptureError, "size limit"):
                self.poll()
        self.assertFalse(self.copied)


    def test_conflicting_terminal_receipts_never_publish_success(self):
        self.completed_pair()
        self.receipt("failure.json", status="cancelled")
        self.poll()
        with self.assertRaisesRegex(capture.CaptureError, "Conflicting"):
            self.poll(strict=True)
        self.assertTrue((self.destination / "failure.json").is_file())
        self.assertFalse((self.destination / "success.json").exists())
