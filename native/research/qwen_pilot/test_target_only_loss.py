"""Trainer regression tests and an opt-in CUDA loss/gradient check."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import train_qwen_pilot as trainer


class TargetOnlyLossTest(unittest.TestCase):
    def test_train_requests_only_target_predictors_before_the_head(self):
        # Prefix contains an EOS, completion has two words and its own EOS, and
        # the chat template leaves one ignored token after the completion.
        prefix = [1, 3, 9, 2]
        full = prefix + [7, 8, 3, 12]
        labels = trainer.completion_labels(prefix, full, 3)
        self.assertEqual(labels, [-100, -100, -100, -100, 7, 8, 3, -100])

        torch = Mock()
        model = Mock()
        batch = {name: MagicMock() for name in ("input_ids", "pixel_values", "labels")}
        for tensor in batch.values():
            tensor.to.return_value = tensor
        batch["input_ids"].shape = batch["labels"].shape = (1, len(full))
        positions = Mock(name="predictor_positions_3_4_5")
        positions.numel.return_value = 3
        masked = batch["labels"][0, 1:]
        selected_labels = Mock(name="target_tokens_7_8_3")
        masked.__ne__.return_value.nonzero.return_value = (positions,)
        first_label = Mock(item=Mock(return_value=-100))
        batch["labels"].__getitem__.side_effect = (
            lambda index: first_label if index == (0, 0) else masked
        )
        masked.__getitem__.return_value = selected_labels
        logits = MagicMock()
        loss = MagicMock()
        loss.detach.return_value.item.return_value = 0.5
        model.return_value = SimpleNamespace(logits=logits, loss=loss)
        torch.nn.functional.cross_entropy.return_value = loss
        parameter = Mock(requires_grad=True)
        model.parameters.return_value = [parameter]
        model.named_parameters.return_value = [("text.q_proj.lora_B.default.weight", parameter)]

        with patch.object(trainer, "save_json"):
            trainer.train(torch, model, {"clip": batch}, Path("."), 1)

        self.assertEqual(model.call_count, trainer.GRAD_ACCUMULATION)
        for call in model.call_args_list:
            self.assertEqual(call.kwargs, {
                "input_ids": batch["input_ids"],
                "pixel_values": batch["pixel_values"],
                "use_cache": False,
                "logits_to_keep": positions,
            })
        masked.__ne__.assert_called_with(-100)
        masked.__ne__.return_value.nonzero.assert_called_with(as_tuple=True)
        masked.__getitem__.assert_called_with(positions)
        torch.nn.functional.cross_entropy.assert_called_with(
            logits[0].float(), selected_labels,
        )


def cuda_self_test():
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("The loss self-test requires the cloud CUDA runtime")
    torch.manual_seed(17631)
    torch.set_float32_matmul_precision("highest")
    eos = 3
    prefix = [1, eos, 6, 2]
    full = prefix + [7, 8, eos, 12]
    labels = torch.tensor([trainer.completion_labels(prefix, full, eos)], device="cuda")
    input_ids = torch.tensor([full], device="cuda")
    hidden = torch.randn(1, len(full), 5, device="cuda", requires_grad=True)
    head = torch.nn.Linear(5, 17, device="cuda")
    parameters = (hidden, head.weight, head.bias)
    loss_fn = torch.nn.functional.cross_entropy
    for targets, predictors in (
        (labels, [3, 4, 5]),
        (labels.masked_fill(input_ids == 8, -100), [3, 5]),
    ):
        # Match Transformers ForCausalLMLoss, including the ignored last row.
        shifted = torch.nn.functional.pad(targets, (0, 1), value=-100)[:, 1:]
        dense_loss = loss_fn(head(hidden).float().flatten(0, 1), shifted.flatten())
        dense_grads = torch.autograd.grad(dense_loss, parameters)

        def model(*, input_ids, use_cache, logits_to_keep):
            assert not use_cache
            assert input_ids.shape == targets.shape == hidden.shape[:2]
            assert logits_to_keep.tolist() == predictors
            return SimpleNamespace(logits=head(hidden[:, logits_to_keep, :]))

        selected_loss = trainer.completion_loss(torch, model, {
            "input_ids": input_ids, "labels": targets,
        })
        selected_grads = torch.autograd.grad(selected_loss, parameters)
        torch.testing.assert_close(selected_loss, dense_loss, atol=1e-6, rtol=1e-6)
        for selected, dense in zip(selected_grads, dense_grads):
            torch.testing.assert_close(selected, dense, atol=1e-6, rtol=1e-6)
    print("CUDA completion loss and hidden, weight, and bias gradients match dense cross-entropy",
          flush=True)


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["--cuda"]:
        cuda_self_test()
    else:
        unittest.main()
