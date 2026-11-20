import copy
from contextlib import nullcontext
from dataclasses import replace
import hashlib
import importlib
import json
from pathlib import Path
import string
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import test_train_qwen_temporal as temporal_tests


def experiment():
    return importlib.import_module("paired_experiment")


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.output = Path(scratch.name)
        self.packages = {
            "torch": "2.8.0+cu126",
            "torchvision": "0.23.0+cu126",
            "transformers": "5.12.0",
            "peft": "0.20.0",
            "accelerate": "1.12.0",
            "Pillow": "12.2.0",
            "huggingface-hub": "1.14.0",
            "tokenizers": "0.22.2",
            "safetensors": "0.7.0",
            "numpy": "2.2.6",
            "Jinja2": "3.1.6",
        }

    def validate(self, device="NVIDIA L4", *, cuda="12.6", count=1, available=True, bf16=True):
        properties = SimpleNamespace(name=device, total_memory=48 * 1024**3)
        torch = SimpleNamespace(
            version=SimpleNamespace(cuda=cuda),
            cuda=SimpleNamespace(
                is_available=lambda: available,
                device_count=lambda: count,
                get_device_properties=lambda index: properties,
                is_bf16_supported=lambda: bf16,
            ),
        )
        output = Path(tempfile.mkdtemp(dir=self.output))
        module = experiment()
        with patch.object(module, "version", side_effect=self.packages.__getitem__):
            module.validate_runtime(torch, output)
        return output / "runtime_receipt.json"

    def test_supported_devices_are_recorded_verbatim(self):
        for device in ("NVIDIA L4", "NVIDIA L40S"):
            with self.subTest(device=device):
                receipt_path = self.validate(device)
                receipt = json.loads(receipt_path.read_text())
                self.assertEqual(receipt["device"], device)
                self.assertEqual(receipt["total_gpu_bytes"], 48 * 1024**3)
                self.assertEqual(receipt["packages"], self.packages)
                self.assertEqual(receipt["torch_cuda"], "12.6")
                self.assertEqual(receipt["dtype"], "bfloat16")
                self.assertEqual(receipt["attention_implementation"], "sdpa")

    def test_unsupported_devices_are_rejected(self):
        for device in ("NVIDIA L40", "NVIDIA A100-SXM4-80GB", "Other Vendor L40S"):
            with self.subTest(device=device):
                with self.assertRaisesRegex(RuntimeError, "bf16-capable"):
                    self.validate(device)

    def test_supported_devices_require_bf16(self):
        for device in ("NVIDIA L4", "NVIDIA L40S"):
            with self.subTest(device=device):
                with self.assertRaisesRegex(RuntimeError, "bf16-capable"):
                    self.validate(device, bf16=False)

    def test_requires_exactly_one_cuda_gpu(self):
        for settings in ({"available": False}, {"count": 0}, {"count": 2}):
            with self.subTest(settings=settings):
                with self.assertRaisesRegex(RuntimeError, "One cloud CUDA GPU"):
                    self.validate(**settings)

    def test_requires_pinned_cuda_and_packages(self):
        with self.assertRaisesRegex(RuntimeError, "pinned CUDA 12.6"):
            self.validate(cuda="12.8")
        self.packages["torchvision"] = "0.23.0"
        with self.assertRaisesRegex(RuntimeError, "pinned CUDA 12.6"):
            self.validate()


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.rows = []
        for letter in string.ascii_uppercase.replace("J", "").replace("Z", ""):
            payload = f"test image {letter}".encode()
            path = f"isolated/{letter}.png"
            image = self.root / path
            image.parent.mkdir(exist_ok=True)
            image.write_bytes(payload)
            self.rows.append({
                "id": f"image_{letter}", "label": letter, "path": path,
                "sha256": hashlib.sha256(payload).hexdigest(), "source_group": "collection-1",
                "role": "training_eligible", "split": "train", "width": 200, "height": 150,
            })
        self.document = {"schema_version": 1, "role": "training_eligible", "samples": self.rows}
        self.dev = [{"source_group": "original video", "id": "development",
                     "sha256": "e" * 64, "source_path": "path/video.png"}]

    def validate(self):
        return experiment().validate_static_manifest(self.document, self.root, [], self.dev)

    def test_static_admission_checks_manifest_hash_and_reads_verified_sample_bytes(self):
        payload = json.dumps(self.document).encode()
        manifest = self.root / "admission.json"
        manifest.write_bytes(payload)
        sha256 = hashlib.sha256(payload).hexdigest()
        self.assertEqual(experiment().read_pinned_json(manifest, sha256), self.document)
        with self.assertRaisesRegex(ValueError, "digest"):
            experiment().read_pinned_json(manifest, "0" * 64)
        rows = self.validate()
        self.assertEqual(len(rows), 24)
        self.assertEqual(experiment().verified_sample_bytes(self.root, rows["image_A"]), b"test image A")
        (self.root / self.rows[0]["path"]).write_bytes(b"image changed after admission")
        with self.assertRaisesRegex(ValueError, "digest"):
            experiment().verified_sample_bytes(self.root, rows["image_A"])

    def test_static_admission_rejects_development_and_source_group_leakage(self):
        original = copy.deepcopy(self.rows[0])
        for changes in (
            {"source_group": "  ORIGINAL video "},
            {"id": "development"},
            {"sha256": "e" * 64},
            {"path": "path/video.png"},
            {"split": "holdout"},
            {"role": "diagnostic_only"},
        ):
            with self.subTest(changes=changes):
                self.rows[0] = {**original, **changes}
                with self.assertRaises(ValueError):
                    self.validate()

    def test_static_admission_rejects_unsafe_paths_duplicates_and_invalid_labels(self):
        original = copy.deepcopy(self.rows[0])
        for changes in (
            {"path": "../x.png"}, {"path": "/test/A.png"}, {"path": "C:/test/A.png"},
            {"path": r"isolated\A.png"}, {"path": "isolated//A.png"}, {"path": "isolated/./A.png"},
            {"path": self.rows[1]["path"].lower()}, {"id": self.rows[1]["id"]},
            {"sha256": self.rows[1]["sha256"]}, {"sha256": "unknown"},
            {"label": "J"}, {"label": "Z"}, {"label": "I love you"}, {"label": "a"},
            {"source_group": ""}, {"width": 0}, {"height": 385}, {"width": True},
        ):
            with self.subTest(changes=changes):
                self.rows[0] = {**original, **changes}
                with self.assertRaises(ValueError):
                    self.validate()

    def test_reference_only_manifest_and_missing_letter_are_rejected(self):
        experiment()
        self.document["role"] = "reference_only"
        with self.assertRaisesRegex(ValueError, "training_eligible"):
            self.validate()
        self.document["role"] = "training_eligible"
        self.rows.pop()
        with self.assertRaisesRegex(ValueError, "24"):
            self.validate()

    def test_single_frame_jpeg_without_frame_attribute_is_accepted(self):
        class SingleJpeg:
            format = "JPEG"
            size = (200, 150)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def convert(self, mode):
                return mode

        path = self.root / "isolated/A.jpg"
        path.write_bytes((self.root / self.rows[0]["path"]).read_bytes())
        self.rows[0]["path"] = "isolated/A.jpg"
        rows = self.validate()
        image_module = type("ImageModule", (), {"open": staticmethod(lambda stream: SingleJpeg())})
        self.assertEqual(experiment().read_static_image(image_module, self.root, rows["image_A"]), "RGB")

    def test_image_dimension_and_format_must_match_admission(self):
        class FakeImage:
            format = "PNG"
            size = 200, 150
            n_frames = 1

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def convert(self, mode):
                self.assert_mode = mode
                return mode

        class ImageModule:
            def open(self, stream):
                return image

        image = FakeImage()
        rows = self.validate()
        read = experiment().read_static_image
        self.assertEqual(read(ImageModule(), self.root, rows["image_A"]), "RGB")
        for attribute, value in (("size", (200, 151)), ("format", "GIF"), ("n_frames", 2)):
            with self.subTest(attribute=attribute):
                image = FakeImage()
                setattr(image, attribute, value)
                with self.assertRaises(ValueError):
                    read(ImageModule(), self.root, rows["image_A"])


class OrientedVideoInputs(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.admission = self.root / "selected-video" / "sample.json"
        self.admission.parent.mkdir()
        self.out = self.root / "output"
        self.out.mkdir()
        self.static = self.root / "static.json"
        self.static.write_bytes(b"{}")
        self.document = temporal_tests.dense_admission()
        self.document["orientation_policy"] = "validated-displaymatrix-right-angle-v1"
        for clip in self.document["clips"]:
            for frame in clip["frames"]:
                frame.update(display_rotation_degrees=180, applied_rotation_degrees_ccw=180)

    def args(self):
        ex = experiment()
        payload = json.dumps(self.document).encode()
        self.admission.write_bytes(payload)
        return SimpleNamespace(
            dense_admission=self.admission, dense_admission_sha256=ex.sha256(payload),
            static_manifest=self.static, static_manifest_sha256=ex.sha256(b"{}"), steps=1,
        )

    def test_paired_run_rejects_legacy_dense_admission_before_static_input(self):
        ex = experiment()
        self.document.pop("orientation_policy")
        for clip in self.document["clips"]:
            for frame in clip["frames"]:
                frame.pop("display_rotation_degrees")
                frame.pop("applied_rotation_degrees_ccw")
        self.admission = self.admission.with_name("admission.json")
        args = self.args()
        with patch.object(ex.pilot, "DENSE", replace(ex.pilot.DENSE, root=self.admission.parent)), \
                patch.object(ex, "DENSE_ADMISSION_SHA256", args.dense_admission_sha256, create=True), \
                patch.object(ex, "validate_static_manifest", side_effect=AssertionError("Legacy video accepted")):
            with self.assertRaisesRegex(ValueError, "orientation"):
                ex.run(args, self.out, {})

    def test_rejects_incomplete_or_invalid_frame_orientation_and_mismatched_pin(self):
        ex = experiment()
        frame = self.document["clips"][-1]["frames"][-1]
        for field, value in (
            ("display_rotation_degrees", None), ("display_rotation_degrees", True),
            ("display_rotation_degrees", 270), ("applied_rotation_degrees_ccw", 90),
            ("applied_rotation_degrees_ccw", 180.0),
        ):
            with self.subTest(field=field, value=value):
                if value is None:
                    frame.pop(field)
                else:
                    frame[field] = value
                args = self.args()
                with self.assertRaisesRegex(ValueError, "orientation"):
                    ex.load_dense_admission(args.dense_admission, args.dense_admission_sha256)
                frame[field] = 180
        self.args()
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            ex.load_dense_admission(self.admission, "0" * 64)

    def test_paired_run_and_development_controls_read_selected_pngs(self):
        ex = experiment()
        expected_pixels = {}
        for clip_index, clip in enumerate(self.document["clips"], 1):
            pixels = []
            for slot, frame in enumerate(clip["frames"]):
                path = self.admission.parent / frame["path"]
                path.parent.mkdir(exist_ok=True, parents=True)
                image = Image.new("RGB", (2, 1), (clip_index, slot, 1))
                image.putpixel((1, 0), (slot, clip_index, 2))
                image.save(path)
                pixels.append(image.tobytes())
            expected_pixels[clip["id"]] = pixels

        torch = Mock()
        torch.equal.side_effect = lambda first, second: first.data == second.data
        processor = Mock()
        processor.tokenizer.eos_token_id = 3
        processor.apply_chat_template.side_effect = temporal_tests.DenseTrainingTest.encode
        training_clips = [clip for clip in self.document["clips"] if clip["split"] == "train"]
        processor.tokenizer.decode.side_effect = [clip["reference"] for clip in training_clips]
        fake_packages = {
            "torch": torch,
            "peft": SimpleNamespace(LoraConfig=None, PeftModel=None, get_peft_model=None),
            "transformers": SimpleNamespace(
                Qwen3_5ForConditionalGeneration=None,
                AutoProcessor=SimpleNamespace(from_pretrained=lambda *args, **kwargs: processor),
            ),
        }
        args = self.args()
        with patch.dict(sys.modules, fake_packages), patch.object(ex, "validate_runtime"), \
                patch.object(ex.pilot, "tensor_digest", side_effect=temporal_tests.DenseTrainingTest.pixel_digest), \
                patch.object(ex, "validate_static_manifest", return_value={"static": {"label": "A"}}), \
                patch.object(ex, "read_static_image", side_effect=StopIteration("Training inputs ready")):
            with self.assertRaisesRegex(StopIteration, "Training inputs ready"):
                ex.run(args, self.out, {})
        receipt = json.loads((self.out / "experiment.json").read_bytes())
        self.assertEqual(receipt["dense_admission_sha256"], args.dense_admission_sha256)
        for clip in training_clips:
            receipt = json.loads((self.out / "training_inputs" / f"{clip['id']}.json").read_bytes())
            self.assertEqual(receipt["pixel_values_sha256"],
                             ex.sha256(b"".join(expected_pixels[clip["id"]])))

        _, _, development, profile = ex.load_dense_admission(self.admission, args.dense_admission_sha256)
        self.assertEqual(profile.root, self.admission.parent)
        donors = ex.wrong_video_donors(development)

        def cpu_encode(*args, **kwargs):
            tensors = temporal_tests.DenseTrainingTest.encode(*args, **kwargs)
            for tensor in tensors.values():
                tensor.to = lambda device, value=tensor: value
            return tensors

        processor.apply_chat_template.side_effect = cpu_encode
        processor.tokenizer.decode.side_effect = None
        processor.tokenizer.decode.return_value = "sample"
        torch.inference_mode.return_value = nullcontext()
        class Generated:
            def __getitem__(self, key):
                return temporal_tests.FakeTensor([3], (1,))

        model = Mock()
        model.generation_config.eos_token_id = 3
        model.generate.return_value = Generated()
        with patch.object(ex.pilot, "tensor_digest", side_effect=temporal_tests.DenseTrainingTest.pixel_digest):
            ex.evaluate_development(torch, Image, processor, model, development, donors, self.out, profile)

        for clip in development:
            pixels = expected_pixels[clip["id"]]
            expected = {
                "matched": pixels, "shuffled": [pixels[i] for i in profile.shuffled],
                "black": [bytes(6)] * len(pixels),
                "wrong_video": expected_pixels[donors[clip["id"]]["id"]],
            }
            for condition, images in expected.items():
                receipt = json.loads((self.out / clip["id"] / f"{condition}.json").read_bytes())
                self.assertEqual(receipt["model_input"]["ordered_rgb_sha256"],
                                 [ex.sha256(image) for image in images])


class PairedTrainingTests(unittest.TestCase):
    def test_schedules_match_video_exposure_and_isolate_the_static_rng(self):
        ex = experiment()
        video = [f"clip-{index}" for index in range(16)]
        static = [f"image-{index}" for index in range(24)]
        arms = ex.paired_schedule(video, static, steps=40)
        self.assertEqual(arms, ex.paired_schedule(video, static, steps=40))
        different_static = ex.paired_schedule(video, static[:15], steps=40)
        video_batches = [step.video_ids for step in arms["A"]]
        self.assertEqual(video_batches, [step.video_ids for step in arms["B"]])
        self.assertEqual(video_batches, [step.video_ids for step in different_static["B"]])
        self.assertEqual(len(video_batches), 40)
        self.assertTrue(all(len(batch) == 2 for batch in video_batches))
        self.assertEqual({clip: sum(batch.count(clip) for batch in video_batches) for clip in video},
                         dict.fromkeys(video, 5))
        self.assertTrue(all(step.static_id is None for step in arms["A"]))
        self.assertEqual(set(step.static_id for step in arms["B"][:24]), set(static))
        self.assertTrue(all(step.static_id in static for step in arms["B"]))
        for steps in (0, 401):
            with self.assertRaises(ValueError):
                ex.paired_schedule(video, static, steps)

    def test_each_arm_reloads_the_same_saved_initialization_on_a_fresh_base(self):
        ex = experiment()
        with tempfile.TemporaryDirectory() as directory:
            init = Path(directory) / "initial"
            calls = []

            def base_factory():
                base = object()
                calls.append(base)
                return base

            def load_lora(base, path):
                self.assertEqual(path, init)
                return {"base": base, "weights": (path / "adapter_model.safetensors").read_bytes()}

            def save_lora(path):
                path.mkdir()
                (path / "adapter_model.safetensors").write_bytes(b"initial")
                (path / "adapter_config.json").write_bytes(b"{}")

            save_lora(init)
            initial_sha = ex.adapter_digest(init)
            a = ex.load_initialized_adapter(base_factory, load_lora, init, initial_sha)
            a["weights"] = b"trained A"
            b = ex.load_initialized_adapter(base_factory, load_lora, init, initial_sha)
            self.assertEqual(b["weights"], b"initial")
            self.assertIsNot(a["base"], b["base"])
            self.assertEqual(len(calls), 2)
            (init / "adapter_model.safetensors").write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "initialization"):
                ex.load_initialized_adapter(base_factory, load_lora, init, initial_sha)
            self.assertEqual(len(calls), 2)

    def test_same_update_backprops_video_mean_and_point_zero_five_aux_before_clip(self):
        ex = experiment()
        for arm in ("A", "B"):
            with self.subTest(arm=arm):
                events = []

                class Loss:
                    def __init__(self, value):
                        self.value = value

                    def detach(self):
                        return self

                    def item(self):
                        return self.value

                    def __mul__(self, weight):
                        return Loss(self.value * weight)

                    def backward(self):
                        events.append(("backward", self.value))

                class Optimizer:
                    def step(self):
                        events.append("step")

                    def zero_grad(self, set_to_none):
                        self.test.assertTrue(set_to_none)
                        events.append("zero")

                def gradient_norm():
                    events.append("clip")
                    return 1.5

                def loss_for(task, item):
                    events.append(("forward", task, item))
                    return Loss({"v0": 2, "v1": 6, "s0": 8}[item])

                optimizer = Optimizer()
                optimizer.test = self
                static = "s0" if arm == "B" else None
                receipt = ex.update_step(optimizer, ("v0", "v1"), static, loss_for, gradient_norm)
                expected = [("forward", "video", "v0"), ("backward", 1.0),
                            ("forward", "video", "v1"), ("backward", 3.0)]
                if static:
                    expected.extend([("forward", "static", "s0"), ("backward", 0.4)])
                self.assertEqual(events, expected + ["clip", "step", "zero"])
                self.assertEqual(receipt["video_loss"], 4.0)
                self.assertEqual(receipt["static_loss"], 8.0 if static else None)
                self.assertEqual(receipt["objective"], 4.4 if static else 4.0)
                self.assertEqual(receipt["gradient_norm_before_clip"], 1.5)

    def test_development_donors_have_other_sources_and_references(self):
        ex = experiment()
        clips = [{"id": str(i), "source_group": group, "reference": ref}
                 for i, (group, ref) in enumerate((("one", "Hello"), ("two", "Thanks"),
                                                   ("three", "Please"), ("four", "Goodbye")))]
        pairs = ex.wrong_video_donors(clips)
        self.assertEqual({item["id"] for item in pairs.values()}, {clip["id"] for clip in clips})
        for clip in clips:
            self.assertNotEqual(clip["source_group"], pairs[clip["id"]]["source_group"])
            self.assertNotEqual(clip["reference"], pairs[clip["id"]]["reference"])
        clips[1]["source_group"] = clips[0]["source_group"]
        pairs = ex.wrong_video_donors(clips)
        self.assertNotEqual(pairs["0"]["id"], "1")
        with self.assertRaises(ValueError):
            ex.wrong_video_donors(clips[:2])

    def test_generation_records_eos_even_at_the_cap(self):
        ex = experiment()
        self.assertEqual(ex.generation_status([9, 7], 7, 2), "eos")
        self.assertEqual(ex.generation_status([9, 8], [7, 8], 2), "eos")
        self.assertEqual(ex.generation_status([9, 9], 7, 2), "max_tokens")
        with self.assertRaises(RuntimeError):
            ex.generation_status([9], 7, 2)


if __name__ == "__main__":
    unittest.main()

