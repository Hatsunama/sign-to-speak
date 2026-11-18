import hashlib
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from test_train_qwen_pilot import admission

import train_qwen_pilot as trainer


DENSE = trainer.DENSE


def dense_admission():
    document = admission()
    document["frames_per_clip"] = 32
    document["evaluation_role"] = "development_controls"
    document["counts"] = {"train": 16, "holdout": 4}
    document["clips"] = []
    for split, count in document["counts"].items():
        for index in range(count):
            clip_id = f"{split}{index}"
            document["clips"].append({
                "id": clip_id, "split": split,
                "source_group": clip_id, "source_path": f"{clip_id}.mov",
                "sha256": hashlib.sha256(clip_id.encode()).hexdigest(),
                "reference": f"Reference for {clip_id}.",
                "frames": [{"path": f"frames/{clip_id}/{i:02d}.png",
                            "requested_seconds": i / 10, "actual_seconds": i / 10,
                            "source_pts_seconds": i / 10, "duplicate": False}
                           for i in range(32)],
            })
    return document


class FakeTensor:
    def __init__(self, data, shape):
        self.data = data
        self.shape = shape
        self.device = Mock(type="cpu")

    def __getitem__(self, index):
        return self.data[index]

    def numel(self):
        return len(self.data)

    def tolist(self):
        return self.data


class FakeImage:
    size = (320, 180)

    def __init__(self, byte):
        self.byte = byte

    def tobytes(self):
        return self.byte


class DenseTrainingTest(unittest.TestCase):
    def test_dense_admission_requires_all_32_timed_frames(self):
        document = dense_admission()
        train, development = trainer.validate_admission(document, DENSE)
        self.assertEqual((len(train), len(development)), (16, 4))
        self.assertEqual(len(train[0]["frames"]), 32)
        document["clips"][0]["frames"][31:32] = []
        with self.assertRaisesRegex(ValueError, "32"):
            trainer.validate_admission(document, DENSE)

    def test_all_control_images_are_ordered_shuffled_or_black(self):
        clip = dense_admission()["clips"][-1]
        image_module = Mock()
        image_module.new.side_effect = lambda mode, size: FakeImage(b"black")
        processor = Mock()
        processor.apply_chat_template.side_effect = self.encode
        with patch.object(trainer, "frame_image", side_effect=self.read_image), \
                patch.object(trainer, "tensor_digest", side_effect=self.pixel_digest):
            controls = trainer.condition_inputs(image_module, processor, clip, DENSE)
        matched = list(controls["matched"][0]["pixel_values"].data)
        shuffled = list(controls["shuffled"][0]["pixel_values"].data)
        self.assertEqual(matched, [bytes([i]) for i in range(32)])
        self.assertCountEqual(shuffled, matched)
        self.assertTrue(all(a != b for a, b in zip(matched, shuffled)))
        self.assertEqual(controls["blank"][0]["pixel_values"].data, [b"black"] * 32)
        for call in processor.apply_chat_template.call_args_list:
            self.assertEqual([message["role"] for message in call.args[0]], ["user"])
            self.assertTrue(call.kwargs["add_generation_prompt"])
            self.assertNotIn(clip["reference"], repr(call.args[0]))

    def test_training_masks_full_32_frame_prefix_and_only_supervises_target(self):
        clip = dense_admission()["clips"][0]
        processor = Mock()
        processor.apply_chat_template.side_effect = self.encode
        processor.tokenizer.eos_token_id = 3
        processor.tokenizer.decode.return_value = clip["reference"]
        torch = Mock()
        with patch.object(trainer, "frame_image", side_effect=self.read_image), \
                patch.object(trainer, "tensor_digest", side_effect=self.pixel_digest), \
                patch.object(trainer, "save_json"):
            trainer.training_example(torch, Mock(), processor, clip, Path("."), DENSE)
        labels = torch.tensor.call_args.args[0][0]
        self.assertEqual(labels, [-100] * 34 + [111, 3, -100])
        processor.tokenizer.decode.assert_called_once_with(
            [111], clean_up_tokenization_spaces=False,
        )

    def test_dense_rejects_truncated_image_inputs_and_token_overflow(self):
        processor = Mock()
        for images, tokens, message in ((31, 100, "32"), (32, 6081, "budget")):
            processor.apply_chat_template.return_value = {
                "input_ids": FakeTensor([], (1, tokens)),
                "image_grid_thw": FakeTensor([], (images, 3)),
                "pixel_values": FakeTensor([1], (1,)),
            }
            with self.subTest(images=images, tokens=tokens), self.assertRaisesRegex(ValueError, message):
                trainer.tokenize_messages(processor, [], True, DENSE)

    @staticmethod
    def encode(messages, **kwargs):
        pixels = [entry["image"].tobytes() for entry in messages[0]["content"][:-1]]
        prefix = [1] + [9] * len(pixels) + [2]
        ids = prefix if kwargs["add_generation_prompt"] else prefix + [111, 3, 12]
        return {
            "input_ids": FakeTensor([FakeTensor(ids, (len(ids),))], (1, len(ids))),
            "image_grid_thw": FakeTensor([[1, 2, 2]] * len(pixels), (len(pixels), 3)),
            "pixel_values": FakeTensor(pixels, (len(pixels),)),
        }

    @staticmethod
    def pixel_digest(tensor):
        return hashlib.sha256(b"".join(tensor.data)).hexdigest()

    @staticmethod
    def read_image(image_module, path, profile):
        return FakeImage(bytes([int(Path(path).stem)]))


if __name__ == "__main__":
    unittest.main()
