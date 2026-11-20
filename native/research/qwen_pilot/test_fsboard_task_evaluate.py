import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

import fsboard_evaluate as scorer
import fsboard_task_admission as admission
import fsboard_task_controls as controls
import fsboard_task_evaluate as evaluator
import fsboard_task_inputs as gate
import fsboard_task_protocol as protocol
import fsboard_training_intake as intake


EOS = 248046
MODEL_EOS = 248044
GENERATED = 6


def digest(data):
    return hashlib.sha256(data).hexdigest()


def reference(number):
    return ("jazz " if number % 2 else "abc ") + chr(97 + number // 26) + chr(97 + number % 26)


class FakeTokenizer:
    eos_token_id = EOS
    all_special_ids = [1, 2, 3, 4, EOS, MODEL_EOS]

    def decode(self, ids, *, clean_up_tokenization_spaces, skip_special_tokens=False):
        assert clean_up_tokenization_spaces is False
        if skip_special_tokens:
            return "  JAZZ   AA!  " if GENERATED in ids else ""
        return "".join(chr(token) for token in ids)


class FakeProcessor:
    tokenizer = FakeTokenizer()
    vision_start_token_id = 1
    image_token_id = 2
    vision_end_token_id = 3
    image_processor = SimpleNamespace(merge_size=1, patch_size=1, temporal_patch_size=1)

    def apply_chat_template(self, messages, *, tokenize, return_dict, return_tensors,
                            add_generation_prompt, enable_thinking, processor_kwargs):
        assert tokenize and return_dict and return_tensors == "np" and enable_thinking is False
        assert processor_kwargs == {"padding": False, "truncation": False,
                                    "return_attention_mask": True, "return_mm_token_type_ids": True}
        user = messages[0]
        assert user["role"] == "user" and user["content"][-1] == {"type": "text", "text": scorer.PROMPT}
        images = [item["image"] for item in user["content"][:-1]]
        ids = [4]
        grid = []
        for image in images:
            ids += [1] + [2] * (image.width * image.height) + [3]
            grid.append([1, image.height, image.width])
        ids.append(4)
        if not add_generation_prompt:
            ids += [ord(character) for character in messages[1]["content"]] + [EOS, 4]
        else:
            assert len(messages) == 1
        ids = np.array([ids])
        return {
            "input_ids": ids, "attention_mask": np.ones_like(ids),
            "mm_token_type_ids": (ids == 2).astype(np.int64),
            "image_grid_thw": np.array(grid),
            "pixel_values": np.concatenate([np.asarray(image).reshape(-1, 3) for image in images]).astype(np.float32),
        }


class FakeTensor:
    def __init__(self, data):
        self.data = data

    def to(self, device):
        assert device == "cuda"
        return self

    def __getitem__(self, index):
        return self.data[index]

    @property
    def shape(self):
        return self.data.shape


class FakeTorch:
    active = False
    from_numpy = staticmethod(FakeTensor)

    @contextmanager
    def inference_mode(self):
        self.active = True
        try:
            yield
        finally:
            self.active = False


class EvaluationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.processor = FakeProcessor()
        cls.clips = []
        for number in range(64):
            clip_id = f"holdout-{number + 1:03d}"
            count = (8, 9, 32)[number % 3]
            cls.clips.append({
                "id": clip_id, "role": "holdout", "split": "daun_v3-validation",
                "signer_id": f"fictional-signer-{number}",
                "full_video_filename": f"fictional-recording-{number}.mov",
                "reference": reference(number), "original_sha256": digest(clip_id.encode()),
                "sampled_frame_count": count,
                "frames": [
                    {"path": f"frames/{clip_id}/{slot:02d}.png", "width": 1 + number % 2, "height": 1,
                     "sha256": digest(f"{clip_id}-{slot}".encode())} for slot in range(count)
                ],
            })
        cls.by_id = {clip["id"]: clip for clip in cls.clips}
        cls.plan = controls.plan_controls(cls.clips)
        cls.frozen = protocol.frozen_protocol()
        cls.summary = {
            "schema_version": 1, "experiment_id": protocol.EXPERIMENT_ID, "mode": "full",
            "status": "full_media_verified", "training_media_complete": True,
            "clips_verified": gate.ROLE_COUNTS,
            "admission_sha256": digest(b"fictional admission"),
            "proposal_sha256": digest(b"fictional proposal"),
            "protocol_sha256": admission.canonical_sha256(cls.frozen),
            "previous_receipt_sha256": intake.PREVIOUS_RECEIPT_SHA256,
            "sampled_frame_counts": {
                "holdout": [clip["sampled_frame_count"] for clip in cls.clips],
                "development": [8] * 32, "training": [8] * 192,
            },
        }
        cls.roles = {"holdout": cls.clips, "development": [None] * 32, "training": [None] * 192}
        cls.examples = {}
        cls.collisions = []
        with patch.object(controls, "load_frames", side_effect=cls.synthetic_frames):
            for clip in cls.clips:
                clip_id = clip["id"]
                with controls.condition_images(Path("."), cls.by_id, cls.plan, clip_id) as images:
                    rows = cls.examples[clip_id] = {
                        condition: gate.encode_example(cls.processor, images[condition], clip["reference"])[2]
                        for condition in evaluator.CONDITIONS
                    }
                for index, first in enumerate(evaluator.CONDITIONS):
                    for second in evaluator.CONDITIONS[index + 1:]:
                        if rows[first]["pixel_values_sha256"] == rows[second]["pixel_values_sha256"]:
                            cls.collisions.append({"clip": clip_id, "first": first, "second": second})

    @staticmethod
    def synthetic_frames(root, clip, *, min_frames, max_frames):
        assert (min_frames, max_frames) == (8, 32) and clip["role"] == "holdout"
        number = int(clip["id"][-3:])
        return [Image.new("RGB", (slot["width"], slot["height"]), (number, i if number != 1 else 0, 1))
                for i, slot in enumerate(clip["frames"])]

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.verified = gate.Admission(self.root, dict(self.summary), self.roles, self.frozen)
        self.plan_path = self.root / "control-plan.json"
        self.plan_sha = self.write(self.plan_path, self.plan)
        self.report_path = self.root / "control-inputs.json"
        self.report = {
            "status": "ALL_HOLDOUT_CONTROL_INPUTS_ENCODED", "conditions_per_clip": 5,
            "clips": 64, "examples": self.examples, "pixel_collisions": self.collisions,
            "control_plan_sha256": self.plan_sha, "model_scoring_started": False,
            "training_eligible": False,
        }
        self.report_sha = self.write(self.report_path, self.report)
        self.output = self.root / "candidate"
        self.torch = FakeTorch()
        self.calls = []
        self.generated = 0
        self.eval_called = False

    def write(self, path, value):
        raw = (json.dumps(value, indent=2) + "\n").encode()
        path.write_bytes(raw)
        return digest(raw)

    def load(self, *, plan_sha=None, report_sha=None, verified=None):
        return evaluator.load_control_inputs(
            self.verified if verified is None else verified,
            control_plan_path=self.plan_path, control_plan_sha256=plan_sha or self.plan_sha,
            control_inputs_path=self.report_path, control_inputs_sha256=report_sha or self.report_sha,
        )

    def generate(self, *, max_new_tokens, do_sample, num_beams, use_cache,
                 return_dict_in_generate, eos_token_id, **batch):
        self.assertTrue(self.torch.active and self.eval_called)
        self.assertEqual((max_new_tokens, do_sample, num_beams, use_cache, return_dict_in_generate),
                         (32, False, 1, True, False))
        self.assertEqual(eos_token_id, [EOS, MODEL_EOS])
        self.assertEqual(batch.keys(), gate.MODEL_FIELDS)
        prefix = batch["input_ids"].data
        self.assertTrue(all(token in {1, 2, 3, 4} for token in prefix[0]))
        self.calls.append(("generation", prefix.copy(), digest(batch["pixel_values"].data)))
        self.generated += 1
        condition = self.generated % 5
        if condition:
            stop = EOS if condition % 2 else MODEL_EOS
            proposed = [GENERATED, stop, 198, MODEL_EOS]
        else:
            proposed = [GENERATED] * max_new_tokens
        tokens = []
        for token in proposed:
            tokens.append(token)
            if token in eos_token_id:
                break
        return FakeTensor(np.concatenate([prefix, [tokens]], axis=1))

    def completion_loss(self, torch, model, batch):
        self.assertIs(torch, self.torch)
        self.assertTrue(torch.active and self.eval_called)
        self.assertEqual(batch.keys(), gate.MODEL_FIELDS | {"labels"})
        _, prefix, pixels = self.calls[-1]
        self.assertEqual(self.calls[-1][0], "generation")
        self.assertEqual(digest(batch["pixel_values"].data), pixels)
        labels = batch["labels"].data[0]
        self.assertTrue((labels[:prefix.size] == -100).all())
        target = labels[labels != -100]
        self.assertEqual(target[-1], EOS)
        self.assertEqual("".join(map(chr, target[:-1])), reference((self.generated - 1) // 5))
        self.calls.append(("nll",))
        return np.float32(self.generated % 5)

    def evaluate(self, *, generate=None, candidate="base", frames=None, plan=None, receipt=None, eos=MODEL_EOS):
        def eval_mode():
            self.eval_called = True
        model = SimpleNamespace(
            eval=eval_mode, generate=self.generate if generate is None else generate,
            generation_config=SimpleNamespace(eos_token_id=eos),
        )
        if plan is None:
            plan, receipt = self.load()
        with patch.object(controls, "load_frames", side_effect=frames or self.synthetic_frames), \
                patch.object(evaluator.pilot, "completion_loss", side_effect=self.completion_loss):
            return evaluator.evaluate_model(
                self.torch, self.processor, model, self.verified, plan, receipt, self.output, candidate,
            )

    def test_full_run_scores_all_five_conditions_and_keeps_literal_pairings(self):
        summary = self.evaluate()
        self.assertEqual(summary["generation"]["eos_token_ids"], [EOS, MODEL_EOS])
        self.assertEqual([call[0] for call in self.calls], ["generation", "nll"] * 320)
        self.assertEqual(len(list(self.output.glob("*/*.json"))), 320)
        self.assertEqual(summary, json.loads((self.output / "summary.json").read_bytes()))
        self.assertEqual(summary["candidate"], "base")
        self.assertEqual(summary["donor_usage"], self.plan["donor_usage"])
        self.assertTrue(self.collisions)
        self.assertEqual(summary["pixel_collisions"], self.collisions)
        self.assertEqual(summary["paired_reference_nll"]["wrong_video_minus_matched_by_clip"],
                         {clip["id"]: -1.0 for clip in self.clips})
        matched = summary["conditions"]["matched"]
        self.assertEqual(matched["aggregate"]["examples"], 64)
        self.assertEqual(set(matched["by_category"]), set(intake.CATEGORIES))
        self.assertEqual(matched["by_category"]["contains_z"]["examples"], 32)
        self.assertEqual(matched["aggregate"]["stop_counts"], {"eos": 64, "max_tokens": 0})
        self.assertEqual(summary["conditions"]["wrong_video"]["aggregate"]["stop_counts"]["max_tokens"], 64)
        for clip in self.clips:
            for condition in evaluator.CONDITIONS:
                row = json.loads((self.output / clip["id"] / f"{condition}.json").read_bytes())
                expected = self.plan["examples"][clip["id"]]["conditions"][condition]
                self.assertEqual(row["control"], expected)
                self.assertEqual(row["model_input"], self.examples[clip["id"]][condition])
                self.assertEqual(row["eos_token_ids"], [EOS, MODEL_EOS])
                index = evaluator.CONDITIONS.index(condition)
                if index < 4:
                    self.assertEqual(row["generated_token_ids"], [GENERATED, EOS if index % 2 == 0 else MODEL_EOS])
                else:
                    self.assertEqual(row["generated_token_ids"], [GENERATED] * 32)
                self.assertEqual(row["scores"], scorer.score("  JAZZ   AA!  ", clip["reference"]))
                self.assertEqual(row["signer_id"], clip["signer_id"])
                self.assertEqual(row["reference_nll"]["tokens_including_eos"], len(clip["reference"]) + 1)
        with self.assertRaises(FileExistsError):
            self.evaluate()
        self.assertEqual(len(self.calls), 640)

    def test_loader_rejects_changed_bytes_missing_rows_duplicate_keys_and_unrelated_plan(self):
        changed = {**self.report, "examples": {key: value for key, value in self.examples.items()
                                              if key != "holdout-064"}}
        changed_sha = self.write(self.report_path, changed)
        with self.assertRaisesRegex(gate.AdmissionError, "SHA-256"):
            self.load()
        with self.assertRaisesRegex(gate.AdmissionError, "64"):
            self.load(report_sha=changed_sha)
        dup = b'{"status":"anything",' + (json.dumps(self.report).encode())[1:]
        self.report_path.write_bytes(dup)
        with self.assertRaisesRegex(gate.AdmissionError, "duplicate"):
            self.load(report_sha=digest(dup))
        self.write(self.report_path, self.report)
        bad_plan = {**self.plan, "donor_usage": {}}
        bad_plan_sha = self.write(self.plan_path, bad_plan)
        with self.assertRaisesRegex(gate.AdmissionError, "plan disagrees"):
            self.load(plan_sha=bad_plan_sha)

    def test_changed_attested_encoding_blocks_affected_model_call(self):
        clip_id = "holdout-001"
        rows = self.examples[clip_id]
        wrong = {**rows["reordered"], "pixel_values_sha256": digest(b"other pixels")}
        collisions = [row for row in self.collisions if row["clip"] != clip_id
                      or "reordered" not in (row["first"], row["second"])]
        changed = {**self.report, "pixel_collisions": collisions, "examples": {
            **self.examples, clip_id: {**rows, "reordered": wrong},
        }}
        plan, receipt = self.load(report_sha=self.write(self.report_path, changed))
        with self.assertRaisesRegex(gate.AdmissionError, "reordered.*attestation"):
            self.evaluate(plan=plan, receipt=receipt)
        self.assertEqual(self.generated, 1)
        self.assertTrue((self.output / clip_id / "matched.json").is_file())
        self.assertFalse((self.output / clip_id / "reordered.json").exists())
        self.assertFalse((self.output / "summary.json").exists())

    def test_changed_pixels_fail_before_model_call_and_close_borrowed_frames(self):
        borrowed = []
        def changed_frames(root, clip, *, min_frames, max_frames):
            frames = self.synthetic_frames(root, clip, min_frames=min_frames, max_frames=max_frames)
            frames[0].putpixel((0, 0), (0, 0, 0))
            borrowed.extend(frames)
            return frames

        with self.assertRaisesRegex(gate.AdmissionError, "matched.*attestation"):
            self.evaluate(frames=changed_frames)
        self.assertEqual(self.calls, [])
        self.assertFalse((self.output / "summary.json").exists())
        self.assertTrue(borrowed)
        for image in borrowed:
            with self.assertRaisesRegex(ValueError, "closed image"):
                image.getpixel((0, 0))

    def test_invalid_generation_stops_and_cancellation_keep_prior_records(self):
        def fail(**kwargs):
            if self.generated < 5:
                return self.generate(**kwargs)
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.evaluate(generate=fail)
        self.assertEqual(len(list(self.output.glob("*/*.json"))), 5)
        self.assertFalse((self.output / "summary.json").exists())
        self.assertFalse(self.torch.active)

        for index, invalid_tokens in enumerate((
            [GENERATED], [33, 3226, EOS, 198, MODEL_EOS], [MODEL_EOS, GENERATED, EOS],
            [GENERATED] * 32 + [EOS],
        )):
            self.output = self.root / f"invalid-{index}"
            def invalid(**kwargs):
                return FakeTensor(np.concatenate([kwargs["input_ids"].data, [invalid_tokens]], axis=1))
            with self.assertRaises(RuntimeError):
                self.evaluate(generate=invalid)
            self.assertEqual(list(self.output.glob("*/*.json")), [])
            self.assertFalse((self.output / "summary.json").exists())

    def test_candidate_and_admission_changes_fail_before_generation(self):
        with self.assertRaises(gate.AdmissionError):
            self.evaluate(candidate="../bad")
        plan, receipt = self.load()
        self.verified.summary["admission_sha256"] = digest(b"other admission")
        with self.assertRaisesRegex(gate.AdmissionError, "binding"):
            self.evaluate(plan=plan, receipt=receipt)
        self.assertFalse(self.eval_called)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
