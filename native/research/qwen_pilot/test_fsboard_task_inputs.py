"""Tests for the v3 FSboard manifest and processor contracts."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

import fsboard_task_inputs as inputs
from test_fsboard_task_admission import proposal_fixture


class GenerationStopTests(unittest.TestCase):
    def test_pinned_qwen_stop_ids_are_ordered_and_deduplicated(self):
        special_ids = [248044, 248045, 248046]
        for model_eos in (248044, [248044, 248046, 248044]):
            with self.subTest(model_eos=model_eos):
                self.assertEqual(
                    inputs.generation_eos_ids(248046, model_eos, special_ids), (248046, 248044),
                )
        self.assertEqual(inputs.generation_eos_ids(248046, None, special_ids), (248046,))

    def test_rejects_invalid_tokenizer_and_model_stop_ids(self):
        special_ids = [0, 248044, 248046]
        for tokenizer_eos in (None, True, 0, -1, "248046", 248045):
            with self.subTest(tokenizer_eos=tokenizer_eos):
                with self.assertRaisesRegex(inputs.AdmissionError, "tokenizer"):
                    inputs.generation_eos_ids(tokenizer_eos, 248044, special_ids)
        for model_eos in (True, -1, "248044", [], [None], [248044, True], [248045], {"id": 248044}):
            with self.subTest(model_eos=model_eos):
                with self.assertRaisesRegex(inputs.AdmissionError, "model"):
                    inputs.generation_eos_ids(248046, model_eos, special_ids)
        self.assertEqual(inputs.generation_eos_ids(248046, 0, special_ids), (248046, 0))


class Processor:
    start_id, image_id, end_id = 7, 8, 9
    vision_start_token_id, image_token_id, vision_end_token_id = start_id, image_id, end_id
    tokenizer = SimpleNamespace(
        eos_token_id=900, all_special_ids=[7, 8, 9, 900],
        decode=lambda ids, **kwargs: "".join(map(chr, ids)),
    )
    image_processor = SimpleNamespace(patch_size=1, temporal_patch_size=2, merge_size=2)

    def __init__(self, fault=None):
        self.calls = []
        self.fault = fault

    def apply_chat_template(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        generation = kwargs["add_generation_prompt"]
        frame_count = len(messages[0]["content"]) - 1
        prefix = [6] + [7, 8, 9] * frame_count + [5]
        if self.fault == "context":
            prefix += [77] * (6144 - len(prefix) - 31)
        tokens = prefix if generation else prefix + list(map(ord, messages[1]["content"])) + [900, 10]
        batch = {
            "input_ids": np.array([tokens]),
            "mm_token_type_ids": np.array([[int(token == 8) for token in tokens]]),
            "attention_mask": np.ones((1, len(tokens)), dtype=int),
            "image_grid_thw": np.array([[1, 2, 2]] * frame_count),
            "pixel_values": np.zeros((frame_count * 4, 6), dtype=np.float32),
        }
        for index, item in enumerate(messages[0]["content"][:-1]):
            batch["pixel_values"][4 * index:4 * (index + 1)] = item["image"].getpixel((0, 0))[0]
        if self.fault == "target-cap" and not generation:
            tokens = prefix + [50] * 32 + [900, 10]
            batch["input_ids"] = np.array([tokens])
            batch["mm_token_type_ids"] = np.array([[int(token == 8) for token in tokens]])
            batch["attention_mask"] = np.ones((1, len(tokens)), dtype=int)
        if self.fault == "prefix" and not generation:
            batch["input_ids"][0, 0] = 4
        if self.fault == "eos" and not generation:
            batch["input_ids"][batch["input_ids"] == 900] = 100
        if self.fault == "attention":
            batch["attention_mask"][0, 0] = 0
        if self.fault == "geometry":
            batch["image_grid_thw"][0] = [2, 2, 2]
        if self.fault == "alignment":
            batch["mm_token_type_ids"][0, 1] = 1
        if self.fault == "patch-count":
            batch["pixel_values"] = batch["pixel_values"][:-1]
        if self.fault == "pixels" and not generation:
            batch["pixel_values"][0, 0] = -10
        if self.fault == "duplicate-eos" and not generation:
            batch["input_ids"][0, -1] = 900
        if self.fault == "video":
            batch["video_grid_thw"] = batch["image_grid_thw"]
        return batch


class ProcessorTests(unittest.TestCase):
    def setUp(self):
        self.images = [Image.new("RGB", (2, 2), (value, 0, 0)) for value in range(32)]
        self.addCleanup(lambda: [image.close() for image in self.images])

    def test_literal_fs_prompt_order_and_target_only_mask(self):
        processor = Processor()
        prefix, batch, receipt = inputs.encode_example(processor, self.images, "jazz hand")
        self.assertEqual(len(processor.calls), 2)
        for messages, kwargs in processor.calls:
            self.assertEqual(messages[0]["content"][-1]["text"], inputs.evaluation.PROMPT)
            self.assertEqual([item["image"] for item in messages[0]["content"][:-1]], self.images)
            self.assertEqual(kwargs["return_tensors"], "np")
            self.assertFalse(kwargs["enable_thinking"])
        self.assertEqual(batch["labels"].tolist(), [
            [-100] * prefix["input_ids"].shape[1] + list(map(ord, "jazz hand")) + [900, -100]
        ])
        self.assertEqual(receipt["supervised_tokens_including_eos"], 10)
        self.assertEqual(receipt["image_grid_thw"], [[1, 2, 2]] * 32)

    def test_rejects_changed_prefix_and_eos_attention_grid_and_visual_alignment(self):
        for fault, diagnostic in [
            ("prefix", "inference prefix"), ("eos", "end-of-turn"),
            ("attention", "attention"), ("geometry", "grid"),
            ("alignment", "multimodal"), ("pixels", "visual"),
            ("patch-count", "patch"), ("video", "fields"), ("context", "context"),
            ("duplicate-eos", "literal"), ("target-cap", "generation"),
        ]:
            with self.subTest(fault=fault), self.assertRaisesRegex(ValueError, diagnostic):
                inputs.encode_example(Processor(fault), self.images, "jazz hand")

    def test_per_clip_count_accepts_8_17_and_32_without_repeating_frames(self):
        for count in (8, 17, 32):
            with self.subTest(count=count):
                processor = Processor()
                prefix, _, receipt = inputs.encode_example(processor, self.images[:count], "zebra")
                self.assertEqual(receipt["sampled_frame_count"], count)
                self.assertEqual(prefix["pixel_values"][::4, 0].tolist(), list(range(count)))
        with self.assertRaisesRegex(ValueError, "RGB"):
            inputs.encode_example(Processor(), self.images[:7], "zebra")

    def test_does_not_rewrite_the_literal_reference(self):
        with self.assertRaisesRegex(ValueError, "literal"):
            inputs.encode_example(Processor(), self.images, " jazz  hand ")


class RuntimeProcessorTests(unittest.TestCase):
    def setUp(self):
        self.model = {"id": "Qwen/Qwen3.5-0.8B", "revision": "2fc06364715b967f1860aea9cf38778875588b17"}
        self.verified = SimpleNamespace(frozen_protocol={"model": self.model})
        processor = type("Qwen3VLProcessor", (), {"image_processor": SimpleNamespace()})()
        self.load_processor = Mock(return_value=processor)
        self.enterContext(patch.dict(sys.modules, {
            "transformers": SimpleNamespace(AutoProcessor=SimpleNamespace(from_pretrained=self.load_processor)),
        }))
        self.enterContext(patch.object(inputs, "version", return_value=inputs.TRANSFORMERS_VERSION))
        self.inspect_examples = self.enterContext(patch.object(
            inputs, "inspect_examples", return_value={"clips_verified": inputs.ROLE_COUNTS},
        ))

    def test_rejects_local_path_shadowing_pinned_hub_model(self):
        for directory in (True, False):
            with self.subTest(directory=directory), tempfile.TemporaryDirectory() as temporary:
                with contextlib.chdir(temporary):
                    shadow = Path(self.model["id"])
                    shadow.parent.mkdir()
                    if directory:
                        shadow.mkdir()
                    else:
                        shadow.touch()
                    with self.assertRaisesRegex(inputs.AdmissionError, "local path.*pinned Hub model"):
                        inputs.verify_with_cloud_processor(self.verified)
        self.load_processor.assert_not_called()
        self.inspect_examples.assert_not_called()

    def test_loads_public_processor_from_pinned_hub_revision(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.chdir(temporary):
            report = inputs.verify_with_cloud_processor(self.verified)
        self.load_processor.assert_called_once_with(
            self.model["id"], revision=self.model["revision"], token=False, trust_remote_code=False,
        )
        self.inspect_examples.assert_called_once_with(self.verified, self.load_processor.return_value)
        self.assertEqual(report["status"], "INPUTS_VERIFIED")
        self.assertEqual(report["processor_class"], "Qwen3VLProcessor")
        self.assertEqual(report["runtime"]["transformers"], inputs.TRANSFORMERS_VERSION)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary)
        self.proposal = self.root / "proposal.json"
        self.previous = self.root / "prior.json"
        self.document = proposal_fixture()
        payload = json.dumps(self.document).encode("utf-8")
        self.proposal.write_bytes(payload)
        self.proposal_sha = hashlib.sha256(payload).hexdigest()
        self.protocol_sha = inputs.admission_gate.canonical_sha256(self.document["protocol"])
        self.prior = self.enterContext(patch.object(inputs.intake, "read_previous", return_value=[]))
        self.media = self.root / "media"
        self.media.mkdir()
        self.path = self.media / "admission.json"
        self.output = self.root / "verified.json"
        self.records = {}
        for role, clips in self.document["clips"].items():
            self.records[role] = [
                {
                    **clip, "id": f"{role}-{position:03d}", "role": role,
                    "sampled_frame_count": 8 + position % 25,
                    "frames": [{"sha256": "a" * 64}] * (8 + position % 25),
                    "original_path": f"source/{role}-{position:03d}.mp4",
                    "original_sha256": hashlib.sha256(clip["source_path"].encode("utf-8")).hexdigest(),
                }
                for position, clip in enumerate(clips, start=1)
            ]
        self.header = {
            "schema_version": 1, "experiment_id": inputs.protocol.EXPERIMENT_ID,
            "mode": "full", "status": "full_media_verified",
            "proposal_sha256": self.proposal_sha, "protocol_sha256": self.protocol_sha,
            "previous_receipt_sha256": inputs.intake.PREVIOUS_RECEIPT_SHA256,
            "clips_verified": {"holdout": 64, "development": 32, "training": 192},
            "original_bytes": sum(clip["original_bytes"] for clips in self.records.values() for clip in clips),
            "min_frames_per_clip": 8, "max_frames_per_clip": 32, "max_frame_edge": 384,
            "sampled_frame_counts": {
                role: [clip["sampled_frame_count"] for clip in clips] for role, clips in self.records.items()
            },
            "training_media_complete": True,
            "training_eligible": False, "processor_token_contract_verified": False,
            "training_started": False, "model_scoring_started": False,
        }

    def write_receipt(self):
        payload = json.dumps({
            **self.header, "contains_private_signer_ids_recordings_and_targets": True,
            "dataset": self.document["dataset"], "clips": self.records,
        }).encode("utf-8")
        self.path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        (self.media / "summary.json").write_text(
            json.dumps({**self.header, "admission_sha256": digest}), encoding="utf-8",
        )
        return digest

    def load(self, digest):
        return inputs.load_admission(
            admission=self.path, admission_sha256=digest, proposal=self.proposal,
            proposal_sha256=self.proposal_sha, protocol_sha256=self.protocol_sha,
            previous_receipt=self.previous,
        )

    def test_binds_exact_admission_and_frozen_proposal(self):
        digest = self.write_receipt()
        self.assertEqual(len(self.load(digest).clips["training"]), 192)
        self.path.write_bytes(self.path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "admission receipt integrity"):
            self.load(digest)

    def test_rejects_sentinel_missing_and_changed_role_records_even_with_matching_receipt_hash(self):
        original = self.records["training"][0].copy()
        cases = [
            ("sentinel", lambda: self.header.update(mode="sentinel"), "full"),
            ("missing", lambda: self.records["training"].pop(), "count"),
            ("reference", lambda: self.records["training"][0].update(reference="changed"), "proposal"),
            ("source", lambda: self.records["training"][0].update(original_path="source/../01.mp4"), "source"),
            ("frame count", lambda: self.records["training"][0].update(sampled_frame_count=7), "sampled"),
            ("source hash", lambda: self.records["training"][0].update(original_sha256="other"), "source"),
            ("cross-role source hash", lambda: self.records["training"][0].update(
                original_sha256=self.records["holdout"][0]["original_sha256"]), "source"),
        ]
        for case, mutate, diagnostic in cases:
            with self.subTest(case=case):
                before = self.header.copy(), self.records["training"].copy()
                mutate()
                with self.assertRaisesRegex(ValueError, diagnostic):
                    self.load(self.write_receipt())
                self.header, self.records["training"] = before
                self.records["training"][0] = original.copy()

    def test_summary_sample_counts_and_admission_hash_are_bound(self):
        self.header["sampled_frame_counts"]["holdout"][0] += 1
        with self.assertRaisesRegex(ValueError, "sampled frame counts"):
            self.load(self.write_receipt())
        self.header["sampled_frame_counts"]["holdout"][0] -= 1
        digest = self.write_receipt()
        summary = json.loads((self.media / "summary.json").read_text(encoding="utf-8"))
        summary["admission_sha256"] = "a" * 64
        (self.media / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "summary does not bind"):
            self.load(digest)

    def test_existing_source_files_are_size_checked(self):
        source = self.media / "source"
        source.mkdir()
        (source / "holdout-001.mp4").write_bytes(b"wrong size")
        with self.assertRaisesRegex(ValueError, "source video size"):
            self.load(self.write_receipt())

    def test_rejects_proposal_leakage_against_prior_receipt(self):
        self.prior.return_value = [self.records["holdout"][0]]
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.load(self.write_receipt())

    def write_frames(self, count=17):
        clip = self.records["holdout"][0]
        folder = self.media / "frames" / clip["id"]
        folder.mkdir(parents=True)
        clip.update(clip_pts_origin_seconds=0, frame_period_seconds=.06,
                    annotation_start_clip_seconds=.2, annotation_end_clip_seconds=1.8)
        clip["sampled_frame_count"] = count
        clip["frames"] = []
        for slot in range(count):
            path = folder / f"{slot:02d}.png"
            Image.new("RGB", (2, 2), (slot, 0, 0)).save(path)
            actual = .2 + 1.6 * slot / (count - 1)
            clip["frames"].append({
                "path": path.relative_to(self.media).as_posix(),
                "width": 2, "height": 2, "frame_index": slot,
                "duplicate": False, "requested_seconds": actual,
                "actual_seconds": actual, "source_pts_seconds": actual,
                "annotation_offset_seconds": actual - .2,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
        return clip

    def test_frame_reader_checks_actual_png_bytes_and_order(self):
        clip = self.write_frames()
        images = inputs.load_frames(self.media, clip, min_frames=8, max_frames=32)
        self.assertEqual([image.getpixel((0, 0))[0] for image in images], list(range(17)))
        for image in images:
            image.close()
        path = self.media / clip["frames"][-1]["path"]
        Image.new("RGBA", (2, 2)).save(path)
        clip["frames"][-1]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "RGB"):
            inputs.load_frames(self.media, clip)

    def test_rejects_frame_path_traversal_and_duplicate_pts(self):
        clip = self.write_frames()
        sample = clip["frames"][1]
        original = sample.copy()
        for change, diagnostic in [
            ({"path": "../01.png"}, "path"),
            ({"source_pts_seconds": clip["frames"][0]["source_pts_seconds"]}, "order"),
        ]:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, diagnostic):
                sample.update(change)
                inputs.load_frames(self.media, clip)
            sample.update(original)
    def test_rejects_linked_frame_directory(self):
        clip = self.write_frames()
        folder = self.media / "frames"
        external = self.root / "external"
        folder.rename(external)
        try:
            folder.symlink_to(external, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks are unavailable")
        with self.assertRaisesRegex(ValueError, "link"):
            inputs.load_frames(self.media, clip)

    def test_no_processor_is_structural_and_all_roles_are_iterated(self):
        verified = self.load(self.write_receipt())
        with patch.object(inputs, "load_frames", return_value=[]) as loader:
            report = inputs.inspect_examples(verified)
        self.assertEqual(loader.call_count, 288)
        self.assertEqual(report["status"], "STRUCTURAL_VERIFIED")
        self.assertFalse(report["training_eligible"])
        self.assertFalse(report["processor_token_contract_verified"])
        self.assertEqual({role: len(rows) for role, rows in report["examples"].items()},
                         self.header["clips_verified"])

    def test_processor_stub_checks_every_role_without_claiming_actual_runtime_verification(self):
        verified = self.load(self.write_receipt())
        processor = Processor()

        def frames(root, clip, **kwargs):
            return [Image.new("RGB", (2, 2), (slot, 0, 0)) for slot in range(clip["sampled_frame_count"])]

        with patch.object(inputs, "load_frames", side_effect=frames):
            report = inputs.inspect_examples(verified, processor)
        self.assertEqual(len(processor.calls), 288 * 2)
        self.assertEqual(report["status"], "PROCESSOR_CONTRACT_CHECKED")
        self.assertFalse(report["processor_token_contract_verified"])
        for role, rows in report["examples"].items():
            self.assertEqual([row["sampled_frame_count"] for row in rows], self.header["sampled_frame_counts"][role])
            self.assertTrue(all(row["supervised_tokens_including_eos"] > 1 for row in rows))

    def test_cli_does_not_publish_partial_results_if_the_last_example_fails(self):
        digest = self.write_receipt()
        argv = [
            "--admission", str(self.path), "--admission-sha256", digest,
            "--proposal", str(self.proposal), "--proposal-sha256", self.proposal_sha,
            "--protocol-sha256", self.protocol_sha, "--previous-receipt", str(self.previous),
            "--output", str(self.output),
        ]

        def load(root, clip, **kwargs):
            if clip["id"] == "training-192":
                raise inputs.AdmissionError("damaged PNG")
            return []

        with patch.object(inputs, "load_frames", side_effect=load) as loader, contextlib.redirect_stderr(
            io.StringIO(),
        ) as errors, contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(inputs.main(argv), 1)
        self.assertEqual(loader.call_count, 288)
        self.assertIn("training-192 damaged PNG", errors.getvalue())
        self.assertEqual(stdout.getvalue(), "")
        self.assertFalse(self.output.exists())

    def test_atomic_publish_preserves_existing_receipt_and_cleans_failed_staging(self):
        inputs.publish_private(self.output, {"value": "é"})
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8")), {"value": "é"})
        with self.assertRaises(FileExistsError):
            inputs.publish_private(self.output, {"value": "changed"})
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8")), {"value": "é"})
        failed = self.root / "failed.json"
        with patch.object(Path, "hardlink_to", side_effect=OSError("simulated failure")):
            with self.assertRaises(OSError):
                inputs.publish_private(failed, {"value": "partial"})
        self.assertFalse(failed.exists())
        self.assertFalse(list(self.root.glob(".fsboard-inputs-*")))


if __name__ == "__main__":
    unittest.main()

