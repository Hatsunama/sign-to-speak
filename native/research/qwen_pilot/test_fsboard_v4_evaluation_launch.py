"""Pure-Python regressions for the frozen evaluation's captured-artifact binding."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fsboard_v4_evaluation_launch import (
    BindingError, EXPERIMENT_ID, MODEL, PAIR_PINS, verify_pair,
)


class PairBindingTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self.pins = {**PAIR_PINS, "control_plan_sha256": "1" * 64}
        self.saved = {}
        self.write("experiment.json", {
            "schema_version": 1, "experiment_id": EXPERIMENT_ID, "model": MODEL, "pins": self.pins,
        })
        self.write("runtime_receipt.json", {"device": "NVIDIA L40S"})
        sequence = [[f"clip-{2 * (step % 96)}", f"clip-{2 * (step % 96) + 1}"] for step in range(192)]
        schedule = {
            arm: [{"video_ids": ids, "static_id": None if arm == "A" else f"static-{step % 80}"}
                  for step, ids in enumerate(sequence)]
            for arm in ("A", "B")
        }
        schedule_sha = self.write("schedule.json", schedule)
        init_adapter = self.adapter("initialization", b"initialized", "2" * 64)
        arms = {}
        for arm in ("A", "B"):
            self.write(f"{arm}/initialization.json", {
                "shared_adapter_sha256": init_adapter["adapter_sha256"],
                "trainable_parameter_sha256": init_adapter["parameter_sha256"],
            })
            for step in range(1, 193):
                self.write(f"{arm}/steps/{step:03}.json", {"optimizer_steps": step})
            checkpoint = f"{arm}/checkpoint-192"
            final = self.adapter(checkpoint, arm.encode(), ("a" if arm == "A" else "b") * 64, 192)
            summary = {
                "optimizer_steps": 192, "video_microbatches": 384,
                "static_microbatches": 0 if arm == "A" else 192,
                "parameter_sha256": final["parameter_sha256"], "adapter_sha256": final["adapter_sha256"],
                "final_checkpoint": checkpoint,
                "final_checkpoint_receipt_sha256": self.saved[f"{checkpoint}/receipt.json"]["sha256"],
            }
            self.write(f"{arm}/summary.json", summary)
            arms[arm] = summary
        self.success = {
            "schema_version": 1, "experiment_id": EXPERIMENT_ID, "status": "completed_training_pair",
            "model": dict(MODEL), "pins": self.pins.copy(), "schedule_sha256": schedule_sha,
            "initialization_sha256": init_adapter["adapter_sha256"], "arms": arms,
            "processor_token_contract_verified": True, "training_started": True, "model_scoring_started": False,
        }
        self.exit = {
            "returncode": 0, "status": "completed_training_pair", "strict_capture_complete": True,
            "cancellation_requested": False, "orchestration_error_type": None,
            "rejected_child_success": None, "fit_adapter_reused": False, "saved_artifacts": self.saved,
        }

    def write(self, relative, contents):
        data = contents if isinstance(contents, bytes) else json.dumps(contents).encode()
        target = self.artifacts / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        digest = sha256(data).hexdigest()
        self.saved[relative] = {"sha256": digest, "bytes": len(data)}
        return digest

    def adapter(self, directory, weights, parameter_sha, step=None):
        files = {
            name: self.write(f"{directory}/{name}", data)
            for name, data in {"adapter_config.json": json.dumps({
                "base_model_name_or_path": MODEL["id"], "revision": MODEL["revision"],
                "peft_type": "LORA", "r": 8, "lora_alpha": 16, "lora_dropout": 0.0, "bias": "none",
            }).encode(), "adapter_model.safetensors": weights}.items()
        }
        receipt = {"adapter_sha256": sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
                   "parameter_sha256": parameter_sha}
        if step is not None:
            receipt["optimizer_steps"] = step
            receipt["optimizer_sha256"] = self.write(f"{directory}/optimizer.pt", b"optimizer-" + weights)
        self.write(f"{directory}/receipt.json", receipt)
        return receipt

    def attest(self):
        self.success_sha = self.write("success.json", self.success)
        payload = json.dumps(self.exit).encode()
        (self.root / "child-exit.json").write_bytes(payload)
        self.exit_sha = sha256(payload).hexdigest()

    def verify(self):
        return verify_pair(
            self.root, success_sha256=self.success_sha, exit_sha256=self.exit_sha,
            control_plan_sha256=self.pins["control_plan_sha256"],
        )

    def test_exact_bytes_bind_distinct_candidates_and_shared_initialization(self):
        self.attest()
        result = self.verify()
        self.assertEqual(set(result), {"A", "B"})
        for arm, binding in result.items():
            self.assertEqual(binding.checkpoint, self.artifacts / arm / "checkpoint-192")
            self.assertEqual(binding.parameter_sha256, self.success["arms"][arm]["parameter_sha256"])
            self.assertEqual(binding.adapter_sha256, self.success["arms"][arm]["adapter_sha256"])
            self.assertEqual(binding.initialization_sha256, self.success["initialization_sha256"])

    def test_identical_trained_adapters_bind_to_separate_arm_evidence(self):
        checkpoint = "B/checkpoint-192"
        arm_a = self.success["arms"]["A"]
        receipt = self.adapter(checkpoint, b"A", arm_a["parameter_sha256"], 192)
        receipt["optimizer_sha256"] = self.write(f"{checkpoint}/optimizer.pt", b"optimizer-B")
        receipt_sha = self.write(f"{checkpoint}/receipt.json", receipt)
        arm_b = self.success["arms"]["B"]
        arm_b.update(
            adapter_sha256=receipt["adapter_sha256"], parameter_sha256=receipt["parameter_sha256"],
            final_checkpoint_receipt_sha256=receipt_sha,
        )
        self.write("B/summary.json", arm_b)
        self.attest()

        bindings = self.verify()

        self.assertEqual(bindings["A"].adapter_sha256, bindings["B"].adapter_sha256)
        self.assertEqual(bindings["A"].parameter_sha256, bindings["B"].parameter_sha256)
        self.assertNotEqual(bindings["A"].checkpoint_receipt_sha256, bindings["B"].checkpoint_receipt_sha256)
        self.assertEqual(bindings["A"].checkpoint, self.artifacts / "A/checkpoint-192")
        self.assertEqual(bindings["B"].checkpoint, self.artifacts / "B/checkpoint-192")

    def test_rejects_swapped_adapters(self):
        self.attest()
        a = self.artifacts / "A/checkpoint-192/adapter_model.safetensors"
        b = self.artifacts / "B/checkpoint-192/adapter_model.safetensors"
        a_payload, b_payload = a.read_bytes(), b.read_bytes()
        a.write_bytes(b_payload)
        b.write_bytes(a_payload)
        with self.assertRaisesRegex(BindingError, "adapter_model.safetensors"):
            self.verify()

    def test_rejects_fake_or_mixed_terminal_metadata_even_with_fresh_pins(self):
        original = deepcopy((self.success, self.exit))
        mutations = (
            ("pair exit", self.exit, "returncode", False),
            ("pair exit", self.exit, "returncode", 5),
            ("pair exit", self.exit, "strict_capture_complete", 1),
            ("pair exit", self.exit, "cancellation_requested", True),
            ("pair exit", self.exit, "fit_adapter_reused", True),
            ("pair exit", self.exit, "orchestration_error_type", "KeyboardInterrupt"),
            ("pair success", self.success, "status", "success"),
            ("pair success", self.success, "training_started", 1),
            ("pair success", self.success["model"], "revision", "wrong"),
            ("pair success", self.success["pins"], "source_manifest_sha256", "f" * 64),
            ("arm A summary", self.success["arms"], "A", deepcopy(self.success["arms"]["B"])),
        )
        for label, target, key, value in mutations:
            with self.subTest(field=key, value=value):
                before = target[key]
                target[key] = value
                self.attest()
                with self.assertRaisesRegex(BindingError, label):
                    self.verify()
                target[key] = before
        self.assertEqual(self.success, original[0])

    def test_rejects_mutations_after_hashes_are_attested(self):
        self.attest()
        tested = ("A/checkpoint-192/adapter_model.safetensors", "B/checkpoint-192/optimizer.pt",
                  "A/checkpoint-192/adapter_config.json", "B/checkpoint-192/receipt.json", "initialization/receipt.json",
                  "A/initialization.json", "A/summary.json", "success.json", "schedule.json")
        for name in tested:
            with self.subTest(file=name):
                path = self.artifacts / name
                payload = path.read_bytes()
                path.write_bytes(payload + b"x")
                with self.assertRaises(BindingError):
                    self.verify()
                path.write_bytes(payload)
        (self.root / "child-exit.json").unlink()
        with self.assertRaises(BindingError):
            self.verify()

    def test_rejects_missing_history_and_conflicting_failure(self):
        self.attest()
        (self.artifacts / "failure.json").write_bytes(b"{}")
        with self.assertRaises(BindingError):
            self.verify()
        (self.artifacts / "failure.json").unlink()
        del self.saved["A/steps/191.json"]
        self.attest()
        with self.assertRaises(BindingError):
            self.verify()

    def test_rejects_wrong_arm_counts_even_with_matching_captured_summary(self):
        self.success["arms"]["B"]["static_microbatches"] = 191
        self.write("B/summary.json", self.success["arms"]["B"])
        self.attest()
        with self.assertRaisesRegex(BindingError, "arm B summary"):
            self.verify()

    def test_rejects_receipt_when_optimizer_bytes_have_new_capture_hash(self):
        self.write("B/checkpoint-192/optimizer.pt", b"other optimizer")
        self.attest()
        with self.assertRaisesRegex(BindingError, "optimizer"):
            self.verify()

    def test_rejects_unproven_shared_initialization(self):
        self.write("B/initialization.json", {
            "shared_adapter_sha256": self.success["initialization_sha256"],
            "trainable_parameter_sha256": "3" * 64,
        })
        self.attest()
        with self.assertRaisesRegex(BindingError, "initialization"):
            self.verify()
