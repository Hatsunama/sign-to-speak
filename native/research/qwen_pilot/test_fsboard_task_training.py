"""Exercise the v4 training adapter with miniature media and no model runtime."""

import copy
import hashlib
import itertools
import json
from pathlib import Path
import random
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

from PIL import Image

import fsboard_task_inputs as gate
import fsboard_task_protocol as protocol
import fsboard_task_training as training
from test_fsboard_task_inputs import Processor


ROOT = Path(__file__).parent


def write_json(path, document):
    payload = json.dumps(document).encode("utf-8")
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def make_admission(root):
    frozen = protocol.frozen_protocol()
    clips = {
        role: [
            {
                "id": f"{role}-{index:03d}", "role": role,
                "signer_id": f"{role}-signer", "full_video_filename": f"{role}-recording",
                "original_sha256": hashlib.sha256(f"{role}-{index}-original".encode()).hexdigest(),
                "source_path": f"{role}/{index}.mp4", "reference": "jazz hand",
                "sampled_frame_count": (8, 17, 32)[(index - 1) % 3],
                "frames": [{"sha256": hashlib.sha256(str(frame).encode()).hexdigest()}
                           for frame in range((8, 17, 32)[(index - 1) % 3])],
            }
            for index in range(1, count + 1)
        ]
        for role, count in gate.ROLE_COUNTS.items()
    }
    summary = {
        "experiment_id": protocol.EXPERIMENT_ID, "status": "full_media_verified",
        "training_media_complete": True, "clips_verified": gate.ROLE_COUNTS.copy(),
        "admission_sha256": "a" * 64, "proposal_sha256": "b" * 64,
        "protocol_sha256": gate.admission_gate.canonical_sha256(frozen), "previous_receipt_sha256": "d" * 64,
    }
    return gate.Admission(root, summary, clips, frozen)


def images_for(_, clip, **kwargs):
    return [Image.new("RGB", (2, 2), (value, 0, 0))
            for value in range(clip["sampled_frame_count"])]


class Qwen3VLProcessor(Processor):
    def apply_chat_template(self, messages, **kwargs):
        result = super().apply_chat_template(messages, **kwargs)
        self.calls.clear()
        return result


class ScheduleTests(unittest.TestCase):
    def test_two_complete_paired_passes_and_exact_pinned_static_cycle(self):
        prior = ROOT / "paired-80-receipts" / "schedule.json"
        static = training.load_prior_static_ids(prior)
        self.assertEqual(static, tuple(step["static_id"] for step in json.loads(prior.read_bytes())["B"]))
        schedule = training.build_schedule(make_admission(ROOT), static)
        self.assertEqual(len(schedule["A"]), 192)
        self.assertEqual([step.video_ids for step in schedule["A"]],
                         [step.video_ids for step in schedule["B"]])
        self.assertTrue(all(len(step.video_ids) == 2 and step.static_id is None for step in schedule["A"]))
        self.assertEqual([step.static_id for step in schedule["B"]],
                         list(itertools.islice(itertools.cycle(static), 192)))
        ids = [f"training-{index:03d}" for index in range(1, 193)]
        rng = random.Random(17631)
        actual = list(itertools.chain.from_iterable(step.video_ids for step in schedule["A"]))
        for offset in (0, 192):
            rng.shuffle(ids)
            self.assertEqual(actual[offset:offset + 192], ids)
            self.assertEqual(len(set(actual[offset:offset + 192])), 192)

    def test_schedule_rejects_hash_change_and_duplicate_id(self):
        original = (ROOT / "paired-80-receipts" / "schedule.json").read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schedule.json"
            path.write_bytes(original + b" ")
            with self.assertRaisesRegex(ValueError, "digest"):
                training.load_prior_static_ids(path)
            document = json.loads(original)
            document["B"][1]["static_id"] = document["B"][0]["static_id"]
            digest = write_json(path, document)
            with patch.object(protocol, "PREVIOUS_SCHEDULE_SHA256", digest):
                with self.assertRaisesRegex(ValueError, "distinct"):
                    training.load_prior_static_ids(path)

    def test_rejects_wrong_roles_and_cross_role_publisher_groups(self):
        prior = training.load_prior_static_ids(ROOT / "paired-80-receipts" / "schedule.json")
        for field, replacement in (
            ("role", "holdout"), ("id", "holdout-001"),
            ("signer_id", " HOLDOUT-SIGNER "),
            ("full_video_filename", " DEVELOPMENT-RECORDING "),
        ):
            with self.subTest(field=field):
                verified = make_admission(ROOT)
                verified.clips["training"][0][field] = replacement
                with self.assertRaisesRegex(ValueError, "role|overlap"):
                    training.build_schedule(verified, prior)


class InputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.verified = make_admission(self.root)
        self.processor = Qwen3VLProcessor()
        self.torch = SimpleNamespace(from_numpy=lambda array: array)
        self.receipt_path = self.root / "input-receipt.json"
        self.prior_path = self.root / "schedule.json"
        self.static_path = self.root / "manifest.json"
        (self.root / "images").mkdir()
        self.static_manifest, self.prior_ids = self.make_static_fixture()
        previous = json.loads((ROOT / "paired-80-receipts" / "schedule.json").read_bytes())
        for step, static_id in zip(previous["B"], self.prior_ids, strict=True):
            step["static_id"] = static_id
        self.prior_digest = write_json(self.prior_path, previous)
        with patch.object(gate, "load_frames", side_effect=images_for):
            self.report = gate.inspect_examples(self.verified, self.processor)
        self.report.update(
            status="INPUTS_VERIFIED", processor_token_contract_verified=True,
            processor_class="Qwen3VLProcessor", image_processor_class="SimpleNamespace",
            runtime={name: gate.TRANSFORMERS_VERSION if name == "transformers" else "test"
                     for name in ("transformers", "tokenizers", "numpy", "Pillow", "torch", "torchvision")},
        )

    def make_static_fixture(self):
        samples = []
        for index, letter in enumerate(itertools.islice(itertools.cycle(sorted(training.paired.STATIC_LETTERS)), 80)):
            path = self.root / "images" / "temp.jpg"
            with Image.new("RGB", (4, 4), (index * 3, 0, 0)) as image:
                image.save(path, quality=100)
            payload = path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            item = f"static-{digest}"
            relative = f"images/{item}.jpg"
            path.rename(self.root / relative)
            samples.append({
                "id": item, "sha256": digest, "label": letter, "path": relative,
                "source_path": f"{letter}/{letter}{index:04d}_test.jpg", "original_bytes": len(payload),
                "source_group": "danrasband-asl-alphabet-test-v1", "width": 4, "height": 4,
                "split": "train", "role": "training_eligible",
            })
        return {"schema_version": 1, "role": "training_eligible", "samples": samples}, tuple(
            row["id"] for row in reversed(samples)
        )

    def prepare(self, receipt_sha256=None):
        digest = write_json(self.receipt_path, self.report)
        static_digest = write_json(self.static_path, self.static_manifest)
        with patch.multiple(protocol, PREVIOUS_SCHEDULE_SHA256=self.prior_digest, STATIC_MANIFEST_SHA256=static_digest):
            return training.prepare_training(
                self.verified, processor_receipt=self.receipt_path,
                processor_receipt_sha256=digest if receipt_sha256 is None else receipt_sha256,
                prior_schedule=self.prior_path, static_manifest=self.static_path,
            )

    def test_receipt_binding_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "digest"):
            self.prepare(receipt_sha256="e" * 64)
        for key, value in (
            ("status", "PROCESSOR_CONTRACT_CHECKED"), ("processor_token_contract_verified", False),
            ("training_eligible", True), ("model", {"id": "different"}),
            ("protocol_sha256", "e" * 64), ("admission_sha256", "e" * 64),
            ("clips_verified", {"training": 192}),
        ):
            with self.subTest(key=key), patch.dict(self.report, {key: value}):
                with self.assertRaisesRegex(ValueError, "processor receipt"):
                    self.prepare()
        rows = self.report["examples"]["holdout"]
        with patch.dict(self.report["examples"], {"holdout": rows[:-1]}):
            with self.assertRaisesRegex(ValueError, "processor receipt"):
                self.prepare()
        with patch.dict(rows[0], {"original_sha256": "e" * 64}):
            with self.assertRaisesRegex(ValueError, "processor receipt"):
                self.prepare()
        duplicate = copy.deepcopy(rows[0])
        with patch.dict(self.report["examples"], {"holdout": [duplicate] + rows[:-1]}):
            with self.assertRaisesRegex(ValueError, "processor receipt"):
                self.prepare()

    def test_static_coverage_role_path_and_source_bytes_are_checked(self):
        row = self.static_manifest["samples"][0]
        for field, value in (("label", "J"), ("split", "test"), ("role", "holdout"), ("path", "../other.jpg")):
            with self.subTest(field=field), patch.dict(row, {field: value}):
                with self.assertRaisesRegex(ValueError, "static"):
                    self.prepare()
        static_sha = write_json(self.static_path, self.static_manifest)
        self.static_path.write_bytes(self.static_path.read_bytes() + b" ")
        with patch.object(protocol, "STATIC_MANIFEST_SHA256", static_sha), self.assertRaisesRegex(ValueError, "digest"):
            training.load_static_rows(self.verified, self.static_path, self.prior_ids)
        path = self.root / row["path"]
        payload = bytearray(path.read_bytes())
        payload[-1] ^= 1
        path.write_bytes(payload)
        with self.assertRaisesRegex(ValueError, "source bytes"):
            self.prepare()

    def test_lazy_batches_use_actual_count_attested_pixels_and_release_images(self):
        prepared = self.prepare()
        video, static, schedule = prepared.arm_inputs("B", torch=self.torch, processor=self.processor)
        self.assertEqual(tuple(step.static_id for step in schedule[:80]), self.prior_ids)
        opened = []

        def tracked_frames(*args, **kwargs):
            result = images_for(*args, **kwargs)
            opened.extend(result)
            return result

        with patch.object(gate, "load_frames", side_effect=tracked_frames) as load:
            self.assertNotIn("holdout-001", video)
            self.assertNotIn("development-001", video)
            with self.assertRaises(KeyError):
                video["holdout-001"]
            load.assert_not_called()
            for index, count in enumerate((8, 17, 32), 1):
                batch = video[f"training-{index:03d}"]
                self.assertEqual(batch["image_grid_thw"].shape, (count, 3))
                self.assertEqual(batch["pixel_values"][::4, 0].tolist(), list(range(count)))
                self.assertEqual(set(batch), gate.MODEL_FIELDS | {"labels"})
                previous = weakref.ref(batch["pixel_values"])
                del batch
                self.assertIsNone(previous())
            video["training-001"]
            self.assertEqual(load.call_count, 4)
        self.assertEqual(len(opened), 65)
        for image in opened:
            with self.assertRaisesRegex(ValueError, "closed"):
                image.getpixel((0, 0))

        loaded_static = []

        def encode(_, processor, image, label):
            loaded_static.append(image)
            self.assertEqual(image.mode, "RGB")
            return {"label": label}

        with patch.object(training.paired, "static_training_example", side_effect=encode):
            self.assertEqual(static[self.prior_ids[0]], {"label": self.static_manifest["samples"][-1]["label"]})
        with self.assertRaisesRegex(ValueError, "closed"):
            loaded_static[0].getpixel((0, 0))
        _, arm_a_static, _ = prepared.arm_inputs("A", torch=self.torch, processor=self.processor)
        self.assertEqual(len(arm_a_static), 0)

    def test_failed_or_cancelled_encoding_closes_images_and_propagates(self):
        prepared = self.prepare()
        video, _, _ = prepared.arm_inputs("B", torch=self.torch, processor=self.processor)
        for failure in (RuntimeError("processor failure"), KeyboardInterrupt()):
            images = images_for(None, self.verified.clips["training"][0])
            with patch.object(gate, "load_frames", return_value=images), patch.object(
                    gate, "encode_example", side_effect=failure):
                with self.assertRaises(type(failure)):
                    video["training-001"]
                for image in images:
                    with self.assertRaisesRegex(ValueError, "closed"):
                        image.getpixel((0, 0))

        def changed_pixels(*args, **kwargs):
            images = images_for(*args, **kwargs)
            images[0].putpixel((0, 0), (99, 0, 0))
            return images

        with patch.object(gate, "load_frames", side_effect=changed_pixels):
            with self.assertRaisesRegex(ValueError, "attested"):
                video["training-001"]


if __name__ == "__main__":
    unittest.main()
