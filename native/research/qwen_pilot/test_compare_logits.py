# SPDX-License-Identifier: Apache-2.0

"""Binary fixtures for the five-row qwen-backend-probe output contract."""

import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from compare_logits import ComparisonError, compare


REVISION = "b1b770d90d94cad9e9ea5b993bdbc383a522fb28"
ROWS = [[4.0 if token == step else -0.5 - token for token in range(6)] for step in range(5)]


def write_row(directory, step, values):
    (directory / f"logits-{step}.f32").write_bytes(struct.pack(f"<{len(values)}f", *values))


def write_probe(directory, rows, forced):
    directory.mkdir()
    tokens = [0, 1, 2, 3]
    steps = []
    for index, row in enumerate(rows):
        write_row(directory, index, row)
        step = {
            "index": index, "file": f"logits-{index}.f32", "shape": [6],
            "n_past": 5 + index, "elapsed_us": 12 if forced else 30,
            "finite_count": 6, "nonfinite_count": 0, "argmax": row.index(max(row)),
        }
        if index:
            step["decoded_token"] = tokens[index - 1]
        steps.append(step)
    metadata = {
        "schema_version": 1, "status": "finite", "llama_revision": REVISION,
        "ggml_commit": REVISION[:8], "ggml_version": "0.1.0",
        "model": "text.gguf", "mmproj": "vision.gguf", "image": "input.png",
        "image_sha256": "a" * 64, "image_width": 64, "image_height": 48,
        "prompt": "private test prompt", "formatted_prompt": "<image>private test prompt",
        "use_jinja": True, "n_vocab": 6, "dtype": "float32", "byte_order": "little",
        "n_ctx": 32, "n_batch": 16, "n_ubatch": 16, "n_threads": 2,
        "n_gpu_layers": -1 if forced else 0, "mmproj_use_gpu": forced,
        "mmproj_device": "device" if forced else "", "ggml_vk_disable_f16": "1" if forced else "",
        "devices": ["device"] if forced else [], "prefill_n_tokens": 7, "prefill_n_past": 5,
        "forced": forced,
        "chunks": [
            {"type": "text", "n_tokens": 1, "n_pos": 1},
            {"type": "image", "n_tokens": 4, "n_pos": 2},
            {"type": "text", "n_tokens": 2, "n_pos": 2},
        ],
        "image_chunk_count": 1, "prefix_text_token_ids": [4, 5, 2],
        "steps": steps, "continuation_tokens": tokens,
    }
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (directory / "tokens.txt").write_text("0\n1\n2\n3\n", encoding="ascii")


class CompareTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.cpu = self.root / "cpu"
        self.replay = self.root / "replay"
        write_probe(self.cpu, ROWS, forced=False)
        write_probe(self.replay, [[value + 0.001 for value in row] for row in ROWS], forced=True)

    def update_metadata(self, directory, update):
        path = directory / "metadata.json"
        metadata = json.loads(path.read_bytes())
        update(metadata)
        path.write_text(json.dumps(metadata), encoding="utf-8")

    def test_small_perturbation_passes_with_finite_metrics_and_hashes(self):
        report = compare(self.cpu, self.replay)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["aggregate"]["rows_compared"], 5)
        self.assertEqual(report["aggregate"]["top1_agreement_rows"], 5)
        self.assertEqual(report["aggregate"]["logits_compared"], 30)
        self.assertEqual(report["source"]["llama_revision"], REVISION)
        self.assertEqual(report["source"]["image_sha256"], "a" * 64)
        self.assertEqual(report["artifacts"]["reference"]["metadata_sha256"],
                         hashlib.sha256((self.cpu / "metadata.json").read_bytes()).hexdigest())
        self.assertEqual(report["rows"][0]["replay_logits_sha256"],
                         hashlib.sha256((self.replay / "logits-0.f32").read_bytes()).hexdigest())
        for row, reference in zip(report["rows"], ROWS):
            self.assertAlmostEqual(row["normalized_mse"], 6e-6 / sum(x*x for x in reference), places=10)
            self.assertGreater(row["cosine_similarity"], 0.999)
            self.assertAlmostEqual(row["max_absolute_difference"], 0.001, places=6)
        self.assertNotIn("private test prompt", json.dumps(report))
        self.assertNotIn("prefix_text_token_ids", json.dumps(report))

    def test_swapped_binary_rows_are_rejected(self):
        first = self.replay / "logits-0.f32"
        second = self.replay / "logits-1.f32"
        saved = first.read_bytes()
        first.write_bytes(second.read_bytes())
        second.write_bytes(saved)
        with self.assertRaisesRegex(ComparisonError, "argmax"):
            compare(self.cpu, self.replay)

    def test_wrong_decoded_token_is_rejected(self):
        self.update_metadata(self.replay, lambda m: m["steps"][2].update(decoded_token=0))
        with self.assertRaisesRegex(ComparisonError, "decoded_token"):
            compare(self.cpu, self.replay)

    def test_self_consistent_wrong_replay_tokens_are_rejected(self):
        def change(m):
            m["continuation_tokens"][0] = 5
            m["steps"][1]["decoded_token"] = 5

        self.update_metadata(self.replay, change)
        (self.replay / "tokens.txt").write_bytes(b"5\n1\n2\n3\n")
        with self.assertRaisesRegex(ComparisonError, "continuation_tokens"):
            compare(self.cpu, self.replay)

    def test_mismatched_token_file_is_rejected(self):
        (self.replay / "tokens.txt").write_bytes(b"5\n1\n2\n3\n")
        with self.assertRaisesRegex(ComparisonError, "tokens.txt"):
            compare(self.cpu, self.replay)

    def test_cpu_continuation_must_follow_binary_argmax(self):
        self.update_metadata(self.cpu, lambda m: m["steps"][0].update(argmax=1))
        write_row(self.cpu, 0, ROWS[1])
        with self.assertRaisesRegex(ComparisonError, "reference.*argmax"):
            compare(self.cpu, self.replay)

    def test_changed_input_fields_are_rejected(self):
        mutations = {
            "image_sha256": lambda m: m.update(image_sha256="b" * 64),
            "prefix_text_token_ids": lambda m: m["prefix_text_token_ids"].__setitem__(0, 0),
            "prompt": lambda m: m.update(prompt="sensitive different text"),
            "formatted_prompt": lambda m: m.update(formatted_prompt="sensitive formatted text"),
            "n_ctx": lambda m: m.update(n_ctx=64),
            "ggml_version": lambda m: m.update(ggml_version="bad version"),
            "llama_revision": lambda m: m.update(llama_revision="f" * 40),
            "n_past": lambda m: m["steps"][4].update(n_past=10),
        }
        for field, change in mutations.items():
            with self.subTest(field=field):
                path = self.replay / "metadata.json"
                original = path.read_bytes()
                self.update_metadata(self.replay, change)
                try:
                    with self.assertRaisesRegex(ComparisonError, field) as error:
                        compare(self.cpu, self.replay)
                    self.assertNotIn("sensitive", str(error.exception))
                finally:
                    path.write_bytes(original)

    def test_changed_consistent_prefix_layout_is_rejected(self):
        def change(m):
            m["chunks"][1]["n_pos"] += 1
            m["prefill_n_past"] += 1
            for step in m["steps"]:
                step["n_past"] += 1

        self.update_metadata(self.replay, change)
        with self.assertRaisesRegex(ComparisonError, "chunks|prefill_n_past"):
            compare(self.cpu, self.replay)

    def test_nonfinite_values_are_rechecked_from_binary(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                row = list(ROWS[0])
                row[-1] = value
                write_row(self.replay, 0, row)
                with self.assertRaisesRegex(ComparisonError, "nonfinite"):
                    compare(self.cpu, self.replay)

    def test_exact_row_size_required(self):
        for row in (ROWS[0][:-1], ROWS[0] + [0.0]):
            with self.subTest(size=len(row)):
                write_row(self.replay, 0, row)
                with self.assertRaisesRegex(ComparisonError, "size"):
                    compare(self.cpu, self.replay)

    def test_zero_norm_is_rejected(self):
        write_row(self.replay, 0, [0.0] * 6)
        with self.assertRaisesRegex(ComparisonError, "norm"):
            compare(self.cpu, self.replay)

    def test_reports_gross_scaling_as_guard_failure_even_with_same_argmax(self):
        write_row(self.replay, 0, [value * 2 for value in ROWS[0]])
        report = compare(self.cpu, self.replay)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["aggregate"]["top1_agreement_rows"], 5)
        self.assertEqual(report["aggregate"]["normalized_mse_pass_rows"], 4)
        self.assertAlmostEqual(report["rows"][0]["normalized_mse"], 1.0)

    def test_tied_cpu_logits_yield_zero_margin_and_lowest_id_argmax(self):
        row = [4.0, 4.0, *ROWS[0][2:]]
        write_row(self.cpu, 0, row)
        write_row(self.replay, 0, [*row[:1], 4.0005, *row[2:]])
        self.update_metadata(self.replay, lambda m: m["steps"][0].update(argmax=1))
        report = compare(self.cpu, self.replay)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["rows"][0]["cpu_margin"], 0.0)
        self.assertEqual(report["rows"][0]["reference_argmax"], 0)
        self.assertFalse(report["rows"][0]["top1_agreement"])
        self.assertEqual(report["aggregate"]["top1_agreement_rows"], 4)

    def invoke(self, *options):
        return subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("compare_logits.py")),
                               str(self.cpu), str(self.replay), *options], capture_output=True, text=True)

    def test_cli_writes_only_new_output_file(self):
        output = self.root / "report.json"
        first = self.invoke("--output", str(output))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, "")
        saved = output.read_bytes()
        self.assertEqual(json.loads(saved)["status"], "pass")
        write_row(self.replay, 0, [value * 2 for value in ROWS[0]])
        second = self.invoke("--output", str(output))
        self.assertEqual(second.returncode, 2)
        self.assertEqual(output.read_bytes(), saved)
        self.assertEqual(second.stdout, "")

    def test_cli_exit_codes_distinguish_guard_failure_from_invalid_input(self):
        write_row(self.replay, 0, [value * 2 for value in ROWS[0]])
        failed = self.invoke()
        self.assertEqual(failed.returncode, 1, failed.stderr)
        self.assertEqual(json.loads(failed.stdout)["status"], "fail")
        write_row(self.replay, 0, [math.nan] * 6)
        invalid = self.invoke()
        self.assertEqual(invalid.returncode, 2, invalid.stderr)
        self.assertEqual(json.loads(invalid.stdout)["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
