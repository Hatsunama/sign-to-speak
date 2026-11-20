"""Mock-only coverage of FSboard's bounded six-clip training fit smoke."""

import argparse
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import fsboard_evaluate as scorer
import fsboard_task_fit as fit


ASSISTANT_EOS = 248046
MODEL_EOS = 248044


def clip(index, reference, role="training"):
    return {"id": f"{role}-{index:03}", "role": role, "reference": reference}


class Tensor:
    def __init__(self, tokens):
        self.tokens = tokens
        self.shape = (1, len(tokens))

    def to(self, device):
        assert device == "cuda"
        return self

    def tolist(self):
        return self.tokens

    def __getitem__(self, index):
        return Tensor(self.tokens[index[1]])


class FitSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.clips = tuple(clip(i, f"example {name}") for i, name in enumerate(
            ["one", "two", "three", "four", "five", "six"], 1,
        ))
        self.torch = NS(optim=NS(AdamW=Mock(return_value=Mock())), cuda=NS(synchronize=Mock()),
                        inference_mode=nullcontext)
        self.model = Mock()
        self.model.parameters.return_value = [NS(requires_grad=True)]
        self.progress = {"stage": "training", "optimizer_updates": 0, "completed_checkpoints": []}

    def test_selection_is_first_six_distinct_literal_training_references(self):
        duplicate = clip(7, self.clips[0]["reference"])
        later = clip(8, "unused")
        self.assertEqual(fit.select_training_clips([self.clips[0], duplicate, *self.clips[1:], later]), self.clips)
        with self.assertRaisesRegex(fit.cloud.RunError, "training"):
            fit.select_training_clips([clip(1, "holdout", "holdout"), *self.clips])
        with self.assertRaisesRegex(fit.cloud.RunError, "six"):
            fit.select_training_clips([self.clips[0], duplicate, *self.clips[1:5]])

    def test_checkpoint_separates_prefix_and_teacher_inputs_and_uses_pinned_score(self):
        self.model.generation_config.eos_token_id = MODEL_EOS
        prefix = {key: Tensor([1, 2]) for key in fit.gate.MODEL_FIELDS}
        teacher = {key: Tensor([1, 2, 33, 3226, ASSISTANT_EOS]) for key in fit.gate.MODEL_FIELDS}
        teacher["labels"] = Tensor([-100, -100, 33, 3226, ASSISTANT_EOS])
        receipt = {"supervised_tokens_including_eos": 3, "eos_token_id": ASSISTANT_EOS}
        processor = NS(tokenizer=NS(
            eos_token_id=ASSISTANT_EOS, all_special_ids=[MODEL_EOS, ASSISTANT_EOS],
            decode=Mock(return_value="  EXAMPLE  ONE "),
        ))

        def generate(**kwargs):
            eos = kwargs.get("eos_token_id", [MODEL_EOS])
            tokens = [1, 2]
            for token in [33, 3226, ASSISTANT_EOS, 198, MODEL_EOS]:
                tokens.append(token)
                if token in eos:
                    break
            return Tensor(tokens)

        self.model.generate.side_effect = generate
        loss = Mock(return_value=Mock(item=Mock(return_value=2.5)))
        with patch.object(fit.pilot, "completion_loss", loss):
            report = fit.assess(self.torch, processor, self.model, self.clips[:1],
                                lambda row: (prefix, teacher, receipt), 0, "aaa")
        row, = report["examples"]
        self.assertEqual(row["generated_token_ids"], [33, 3226, ASSISTANT_EOS])
        self.model.generate.assert_called_once_with(
            **prefix, max_new_tokens=32, do_sample=False, num_beams=1, use_cache=True,
            return_dict_in_generate=False, eos_token_id=[ASSISTANT_EOS, MODEL_EOS],
        )
        self.assertEqual(self.model.generation_config.eos_token_id, MODEL_EOS)
        loss.assert_called_once_with(self.torch, self.model, teacher)
        self.assertEqual(row["eos_token_ids"], [ASSISTANT_EOS, MODEL_EOS])
        self.assertEqual(report["eos_token_ids"], [ASSISTANT_EOS, MODEL_EOS])
        self.assertEqual(row["stop"], "eos")
        self.assertEqual(row["scores"], scorer.score("EXAMPLE  ONE", "example one"))
        self.assertEqual(report["aggregate"], scorer.aggregate([row]))
        self.assertEqual(report["mean_matched_teacher_nll"], 2.5)

        self.model.generate.side_effect = None
        self.model.generate.return_value = Tensor([1, 2, 50, MODEL_EOS])
        with patch.object(fit.pilot, "completion_loss", loss):
            secondary = fit.assess(self.torch, processor, self.model, self.clips[:1],
                                   lambda row: (prefix, teacher, receipt), 0, "aaa")
        self.assertEqual(secondary["examples"][0]["stop"], "eos")

        for eos in (ASSISTANT_EOS, MODEL_EOS):
            with self.subTest(ignored_eos=eos):
                self.model.generate.return_value = Tensor([1, 2, 50, eos, 198, MODEL_EOS])
                with patch.object(fit.pilot, "completion_loss") as invalid_loss:
                    with self.assertRaisesRegex(fit.cloud.RunError, "after EOS"):
                        fit.assess(self.torch, processor, self.model, self.clips[:1],
                                   lambda row: (prefix, teacher, receipt), 0, "aaa")
                invalid_loss.assert_not_called()

        self.model.generate.return_value = Tensor([1, 2, *([50] * 32)])
        with patch.object(fit.pilot, "completion_loss", loss):
            capped = fit.assess(self.torch, processor, self.model, self.clips[:1],
                                lambda row: (prefix, teacher, receipt), 0, "aaa")
        self.assertEqual(capped["aggregate"]["stop_counts"], {"eos": 0, "max_tokens": 1})

    def test_attested_prefix_loader_closes_images_on_success_and_error(self):
        images = [Mock() for _ in range(8)]
        prefix = {"input_ids": object()}
        full = {"labels": object()}
        receipt = {"sampled_frame_count": 8}
        verified = NS(root=self.root, frozen_protocol={"input": {
            "min_ordered_rgb_stills": 8, "max_ordered_rgb_stills": 32,
            "dense_max_sequence_tokens": 6144,
        }})
        self.torch.from_numpy = Mock(side_effect=lambda array: array)
        row = self.clips[0]
        with (
            patch.object(fit.gate, "load_frames", return_value=images) as load,
            patch.object(fit.gate, "encode_example", return_value=(prefix, full, receipt)) as encode,
        ):
            self.assertEqual(fit.encode_checkpoint_clip(
                verified, {row["id"]: receipt}, self.torch, object(), row,
            ), (prefix, full, receipt))
            load.assert_called_once_with(self.root, row, min_frames=8, max_frames=32)
            self.assertEqual(encode.call_args.args[1:], (images, row["reference"]))
            self.assertEqual(encode.call_args.kwargs, {
                "min_frames": 8, "max_frames": 32, "context": 6144, "generation_cap": 32,
            })
            with self.assertRaisesRegex(fit.cloud.RunError, "attested"):
                fit.encode_checkpoint_clip(verified, {row["id"]: {}}, self.torch, object(), row)
            encode.side_effect = KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):
                fit.encode_checkpoint_clip(verified, {row["id"]: receipt}, self.torch, object(), row)
        for image in images:
            self.assertEqual(image.close.call_count, 3)

    def exercise_updates(self, *, at_50, at_100, digests=("after50", "after100"), update=None):
        ids = tuple(row["id"] for row in self.clips)
        batch = {"input_ids": Tensor([1, 2]), "labels": Tensor([-100, 2])}
        training_requests = []
        optimizer_ids = []
        update_requests = []

        class Video:
            def __getitem__(self, item):
                assert item in ids
                training_requests.append(item)
                return batch

        def default_update(optimizer, video_ids, static_id, loss_for, clip_grad, sync):
            assert static_id is None
            optimizer_ids.append(id(optimizer))
            update_requests.append(tuple(video_ids))
            for item in video_ids:
                loss_for("video", item)
            return {"video_loss": 2.0, "gradient_norm_before_clip": 1.0}

        def assess_checkpoint(step, digest):
            self.assertEqual(self.progress["optimizer_updates"], step)
            score, nll = {0: (0, 4), 50: at_50, 100: at_100}[step]
            return {
                "optimizer_updates": step, "parameter_sha256": digest, "aggregate": {"exact_matches": score},
                "mean_matched_teacher_nll": nll,
            }

        with (
            patch.object(fit.paired, "update_step", side_effect=update or default_update),
            patch.object(fit.paired, "parameter_digest", side_effect=digests),
            patch.object(fit.pilot, "completion_loss", return_value=object()) as loss,
            redirect_stdout(StringIO()),
        ):
            result = fit.run_updates(self.torch, self.model, Video(), ids, assess_checkpoint,
                                     "initial", self.root, self.progress)
        self.assertEqual(self.torch.optim.AdamW.call_count, 1)
        self.assertEqual(len(set(optimizer_ids)), 1)
        self.assertEqual(len(training_requests), 2 * self.progress["optimizer_updates"])
        self.assertEqual(loss.call_count, len(training_requests))
        self.assertEqual(training_requests, [ids[i % 6] for i in range(len(training_requests))])
        self.assertEqual(update_requests, list(zip(training_requests[::2], training_requests[1::2])))
        return result

    def test_stops_at_50_with_exact_and_relative_nll_threshold(self):
        result = self.exercise_updates(at_50=(6, 3.0), at_100=(0, 4.0))
        self.assertEqual([row["optimizer_updates"] for row in result], [0, 50])
        self.assertTrue(result[-1]["fit_guard_passed"])
        self.assertEqual(result[-1]["relative_teacher_nll_reduction"], 0.25)
        self.assertFalse((self.root / "checkpoint-100.json").exists())

    def test_same_optimizer_continues_to_100_when_only_one_metric_passes(self):
        result = self.exercise_updates(at_50=(6, 3.01), at_100=(5, 2.0))
        self.assertEqual([row["optimizer_updates"] for row in result], [0, 50, 100])
        self.assertFalse(result[-1]["fit_guard_passed"])
        self.assertEqual(result[-1]["parameter_sha256"], "after100")

    def test_unchanged_parameters_fail_and_preserve_baseline(self):
        with self.assertRaisesRegex(fit.cloud.RunError, "parameters did not change"):
            self.exercise_updates(at_50=(6, 2), at_100=(6, 2), digests=("initial",))
        self.assertTrue((self.root / "checkpoint-000.json").is_file())
        self.assertFalse((self.root / "checkpoint-050.json").exists())

    def test_nonfinite_step_fails_before_advancing_progress(self):
        with self.assertRaisesRegex(fit.cloud.RunError, "nonfinite"):
            self.exercise_updates(at_50=(6, 2), at_100=(6, 2),
                                  update=lambda *args: {"video_loss": float("nan"),
                                                        "gradient_norm_before_clip": 1.0})
        self.assertEqual(self.progress["optimizer_updates"], 0)

    def test_source_manifest_must_bind_fit_entry_point(self):
        payload = Path(fit.__file__).read_bytes()
        source_row = {"file": Path(fit.__file__).name, "sha256": sha256(payload).hexdigest(), "bytes": len(payload)}
        manifest = self.root / "source.json"
        with patch.object(fit.cloud, "verify_source", return_value={"other.py": "example"}):
            for rows in ([], [{**source_row, "bytes": 0}], [source_row, source_row]):
                manifest.write_text(json.dumps(rows), encoding="utf-8")
                with self.assertRaisesRegex(fit.cloud.RunError, "fit"):
                    fit.verify_source(manifest, sha256(manifest.read_bytes()).hexdigest())
            manifest.write_text(json.dumps([source_row]), encoding="utf-8")
            result = fit.verify_source(manifest, sha256(manifest.read_bytes()).hexdigest())
        self.assertEqual(result[source_row["file"]], source_row["sha256"])

    def test_source_and_trusted_processor_gates_run_before_model_runtime(self):
        args = NS(**{name: self.root / name for name in (
            "source_manifest", "fit_protocol", "admission", "proposal", "previous_receipt", "processor_receipt",
            "prior_schedule", "static_manifest", "output",
        )}, **{name: "a" * 64 for name in (
            "source_manifest_sha256", "fit_protocol_sha256", "admission_sha256", "proposal_sha256",
            "protocol_sha256", "processor_receipt_sha256",
        )})
        with (
            patch.object(fit, "verify_source", side_effect=fit.cloud.RunError("source")),
            patch.object(fit.gate, "load_admission") as admission,
            patch.object(fit.cloud, "load_runtime") as runtime,
        ):
            with self.assertRaisesRegex(fit.cloud.RunError, "source"):
                fit.run_fit(args, self.progress)
            admission.assert_not_called()
            runtime.assert_not_called()
        args.fit_protocol.write_text(json.dumps(fit.FIT_POLICY), encoding="utf-8")
        args.fit_protocol_sha256 = sha256(args.fit_protocol.read_bytes()).hexdigest()
        with (
            patch.object(fit, "verify_source", return_value={}),
            patch.object(fit.gate, "load_admission", return_value=NS()) as admission,
            patch.object(fit.training, "prepare_training", side_effect=fit.gate.AdmissionError("untrusted")) as prepare,
            patch.object(fit.cloud, "load_runtime") as runtime,
        ):
            with self.assertRaisesRegex(fit.gate.AdmissionError, "untrusted"):
                fit.run_fit(args, self.progress)
            self.assertEqual(admission.call_args.kwargs["admission_sha256"], args.admission_sha256)
            self.assertEqual(prepare.call_args.kwargs["processor_receipt_sha256"], args.processor_receipt_sha256)
            runtime.assert_not_called()
            args.fit_protocol.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(fit.cloud.RunError, "protocol"):
                fit.run_fit(args, self.progress)
            self.assertEqual(admission.call_count, 1)

    def test_failure_and_cancel_receipts_are_private_and_output_is_fresh(self):
        for name, error, expected in (("fail", ValueError("sensitive"), 1),
                                      ("cancel", KeyboardInterrupt(), 130)):
            destination = self.root / name
            args = argparse.Namespace(output=destination)
            previous = signal.getsignal(signal.SIGTERM)
            with (
                patch.object(fit, "parse_args", return_value=args),
                patch.object(fit.sys, "platform", "linux"),
                patch.object(fit, "run_fit", side_effect=error) as run,
                redirect_stderr(StringIO()) as stderr, redirect_stdout(StringIO()),
            ):
                self.assertEqual(fit.main([]), expected)
                self.assertEqual(fit.main([]), 1)
                run.assert_called_once()
            self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
            path = destination / "failure.json"
            self.assertNotIn("sensitive", path.read_text())
            self.assertNotIn("sensitive", stderr.getvalue())
            receipt = json.loads(path.read_bytes())
            self.assertEqual(receipt["status"], "cancelled" if expected == 130 else "failed")
            self.assertFalse((destination / "success.json").exists())
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(destination.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
