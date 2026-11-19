"""CPU-only tests for the static learnability probe."""

from collections import Counter
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import static_learnability as probe


class ProbeTests(unittest.TestCase):
    def samples(self):
        rows = [
            {
                "id": f"selected-{label}", "label": label, "source_path": f"{label}/{label}0007_test.jpg",
                "role": "training_eligible", "split": "train", "width": 200, "height": 200,
            }
            for label in "ABCL"
        ]
        rows.append({**rows[0], "id": "other-A", "source_path": "A/A0001_test.jpg"})
        return {row["id"]: row for row in rows}

    def test_only_approved_source_paths_with_matching_labels(self):
        samples = self.samples()
        self.assertEqual([row["id"] for row in probe.select_rows(samples)], [f"selected-{c}" for c in "ABCL"])
        for invalid in [{"label": "O"}, {"role": "evaluation_only"}, {"width": 201}]:
            with self.subTest(invalid=invalid):
                changed = {**samples, "selected-C": {**samples["selected-C"], **invalid}}
                with self.assertRaises(ValueError):
                    probe.select_rows(changed)
        for changed in (
            {key: row for key, row in samples.items() if key != "selected-A"},
            {**samples, "duplicate": {**samples["selected-A"], "id": "duplicate"}},
        ):
            with self.assertRaises(ValueError):
                probe.select_rows(changed)

    def test_fixed_balanced_schedule_and_checkpoint_bounds(self):
        ids = [row["id"] for row in probe.select_rows(self.samples())]
        schedule = probe.build_schedule(ids)
        self.assertEqual(len(schedule), 200)
        self.assertEqual(Counter(schedule), {item: 50 for item in ids})
        self.assertEqual(schedule, probe.build_schedule(ids))
        for start in range(0, len(schedule), 4):
            self.assertEqual(set(schedule[start:start + 4]), set(ids))
        self.assertGreater(len(set(zip(*(iter(schedule),) * 4))), 1)
        self.assertNotEqual(probe.SAMPLER_SEED, probe.paired.STATIC_SEED)
        self.assertEqual(probe.CHECKPOINT_STEPS, (0, 50, 100, 200))
        with self.assertRaises(ValueError):
            probe.build_schedule(ids + ids[:1])

    def test_single_static_microbatch_and_unit_loss_weight(self):
        optimizer = Mock()
        example = {"labels": Mock(), "pixel_values": Mock()}
        model = Mock()
        parameter = Mock()
        loss = Mock()
        loss.detach.return_value.item.return_value = 2.5
        weighted = loss.__mul__ = Mock()
        clip = Mock()
        clip.return_value.item.return_value = 3.0
        torch = SimpleNamespace(nn=SimpleNamespace(utils=SimpleNamespace(clip_grad_norm_=clip)))
        with patch.object(probe.pilot, "completion_loss", return_value=loss) as completion:
            result = probe.update_step(torch, model, optimizer, [parameter], example)
        completion.assert_called_once_with(torch, model, {key: value.to.return_value for key, value in example.items()})
        weighted.assert_called_once_with(1.0)
        weighted.return_value.backward.assert_called_once_with()
        clip.assert_called_once_with([parameter], 1.0, error_if_nonfinite=True)
        optimizer.step.assert_called_once_with()
        optimizer.zero_grad.assert_called_once_with(set_to_none=True)
        self.assertEqual(result, {"target_ce": 2.5, "gradient_norm_before_clip": 3.0})

    def test_adapter_loader_can_only_open_shared_initialization(self):
        root = Path.cwd().resolve() / "pair"
        expected = "a" * 64
        base_factory, load_lora = Mock(), Mock()
        with patch.object(probe.paired, "adapter_digest", return_value=expected) as digest:
            model = probe.load_initial_adapter(root, expected, base_factory, load_lora)
        self.assertIs(model, load_lora.return_value)
        digest.assert_called_once_with(root / "initialization")
        base_factory.assert_called_once_with()
        load_lora.assert_called_once_with(base_factory.return_value, root / "initialization")
        base_factory.reset_mock()
        load_lora.reset_mock()
        with patch.object(probe.paired, "adapter_digest", return_value="b" * 64):
            with self.assertRaises(ValueError):
                probe.load_initial_adapter(root, expected, base_factory, load_lora)
        base_factory.assert_not_called()
        load_lora.assert_not_called()

    def test_strict_scoring_and_single_unlabeled_black_control(self):
        torch = SimpleNamespace(inference_mode=nullcontext)
        rows = probe.select_rows(self.samples())
        generated = [{"decoded": text, "stop": "eos"} for text in [" a ", "B", "Ｃ", "L", "A"]]
        with patch.object(probe, "predict", side_effect=generated) as predict:
            result = probe.evaluate(torch, Mock(), Mock(), rows, {row["id"]: Mock() for row in rows}, Mock())
        self.assertEqual(predict.call_count, 5)
        self.assertEqual([row["label"] for row in result["predictions"]], list("ABCL"))
        self.assertEqual(result["exact_matches"], 4)
        self.assertEqual(result["distinct_photo_outputs"], 4)
        self.assertTrue(result["visual_learnability_signal"])
        self.assertEqual(result["black"], {"decoded": "A", "stop": "eos"})
        generated[2] = {"decoded": "It is C.", "stop": "max_tokens"}
        with patch.object(probe, "predict", side_effect=generated):
            result = probe.evaluate(torch, Mock(), Mock(), rows, {row["id"]: Mock() for row in rows}, Mock())
        self.assertEqual(result["exact_matches"], 3)
        self.assertFalse(result["visual_learnability_signal"])


if __name__ == "__main__":
    unittest.main()
