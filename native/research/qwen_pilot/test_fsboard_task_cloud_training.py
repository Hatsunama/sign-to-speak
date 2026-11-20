"""Mock the cloud runtime while exercising the paired training entry point."""

from collections import Counter
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, call, patch

import fsboard_task_cloud_training as cloud
import fsboard_task_inputs as gate
import fsboard_task_training as training
import paired_experiment as paired
import train_qwen_pilot as pilot
from test_fsboard_task_training import make_admission


class CloudTrainingTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(TemporaryDirectory()))
        self.output = self.root / "run"
        self.verified = make_admission(self.root)
        ids = tuple("static-" + f"{i:064x}" for i in range(80))
        self.schedule = training.build_schedule(self.verified, ids)
        self.source = self.root / "SOURCE-MANIFEST.json"
        self.source.write_bytes(json.dumps([
            {"file": name, "bytes": len(data), "sha256": sha256(data).hexdigest()}
            for name in sorted(cloud.RUNTIME_SOURCES)
            for data in [Path(cloud.__file__).with_name(name).read_bytes()]
        ]).encode("utf-8"))
        self.source_sha = sha256(self.source.read_bytes()).hexdigest()
        self.control = self.root / "control-plan.json"
        self.control.write_bytes(b"{}")
        self.admit = stack.enter_context(patch.object(gate, "load_admission", return_value=self.verified))
        self.controls = stack.enter_context(patch("fsboard_task_controls.verify_plan"))
        self.prepare = stack.enter_context(patch.object(training, "prepare_training"))
        self.prepare.return_value.arm_inputs.side_effect = self.arm_inputs

        self.torch = NS(manual_seed=Mock(), bfloat16=object(), cuda=Mock(), nn=NS(utils=Mock()))
        self.torch.cuda.max_memory_allocated.return_value = 2000
        self.torch.cuda.max_memory_reserved.return_value = 3000
        self.torch.nn.utils.clip_grad_norm_.return_value.item.return_value = 0.5
        self.torch.save = lambda state, path: path.write_bytes(json.dumps(state).encode("utf-8"))
        self.torch.optim = NS(AdamW=Mock(side_effect=self.new_optimizer))
        self.factory = Mock(side_effect=self.make_model)
        self.processor = Mock()
        self.wrap = Mock(side_effect=lambda model, path, **kwargs: model)
        self.runtime = stack.enter_context(patch.object(cloud, "load_runtime", return_value=(
            self.torch, NS(from_pretrained=self.processor), NS(from_pretrained=self.factory),
            Mock(), Mock(), NS(from_pretrained=self.wrap),
        )))
        stack.enter_context(patch.object(paired, "validate_runtime"))
        self.init = stack.enter_context(patch.object(paired, "create_initial_adapter", side_effect=self.initialize))
        stack.enter_context(patch.object(paired, "parameter_digest", side_effect=self.parameter_digest))
        stack.enter_context(patch.object(pilot, "completion_loss", return_value=Loss()))
        self.train = stack.enter_context(patch.object(paired, "train_arm", wraps=paired.train_arm))
        self.models, self.optimizers = [], []
        self.update_hook = Mock()

    def arm_inputs(self, arm, **kwargs):
        video = {clip["id"]: {} for clip in self.verified.clips["training"]}
        static = {step.static_id: {} for step in self.schedule["B"]} if arm == "B" else {}
        return video, static, self.schedule[arm]

    def make_model(self, *args, **kwargs):
        model = Mock(config=NS(model_type="qwen3_5"))
        model.to.return_value = model
        model.requires_grad_.return_value = model
        model.parameters.return_value = [NS(requires_grad=True, updates=0)]
        model.save_pretrained.side_effect = lambda path, **kw: self.write_adapter(model, path)
        self.models.append(model)
        return model

    @staticmethod
    def write_adapter(model, path):
        path.mkdir()
        (path / "adapter_config.json").write_bytes(b"{}")
        (path / "adapter_model.safetensors").write_bytes(str(model.parameters()[0].updates).encode("ascii"))

    @staticmethod
    def parameter_digest(model):
        return sha256(str(model.parameters()[0].updates).encode("ascii")).hexdigest()

    def initialize(self, torch, model, config, wrap, revision, path):
        model.save_pretrained(path)
        return {"adapter_sha256": paired.adapter_digest(path), "parameter_sha256": self.parameter_digest(model)}

    def new_optimizer(self, params, **kwargs):
        self.assertEqual(params[0].updates, 0)
        optimizer = Mock()
        self.optimizers.append(optimizer)

        def step():
            self.update_hook(len(self.optimizers), params[0].updates)
            params[0].updates += 1

        optimizer.step.side_effect = step
        optimizer.state_dict.side_effect = lambda: {"updates": params[0].updates}
        return optimizer

    def run_entry(self):
        values = {
            "output": self.output, "source-manifest": self.source, "source-manifest-sha256": self.source_sha,
            "admission": self.root / "admission.json", "admission-sha256": "a" * 64,
            "proposal": self.root / "proposal.json", "proposal-sha256": "b" * 64,
            "protocol-sha256": self.verified.summary["protocol_sha256"],
            "previous-receipt": self.root / "previous.json", "prior-schedule": self.root / "prior-schedule.json",
            "processor-receipt": self.root / "processor.json", "processor-receipt-sha256": "1" * 64,
            "control-plan": self.control, "control-plan-sha256": sha256(self.control.read_bytes()).hexdigest(),
            "static-manifest": self.root / "static-manifest.json",
        }
        argv = [part for name, value in values.items() for part in (f"--{name}", str(value))]
        with patch.object(sys, "platform", "linux"), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return cloud.main(argv)

    def report(self, name):
        return json.loads((self.output / name).read_bytes())

    def test_import_does_not_load_model_dependencies(self):
        subprocess.run([
            sys.executable, "-B", "-c", "import sys; import fsboard_task_cloud_training; "
            "assert not {'torch', 'transformers', 'peft'} & sys.modules.keys()",
        ], cwd=Path(cloud.__file__).parent, check=True)

    def test_admission_and_processor_gate_run_before_heavy_imports(self):
        for dependency, stage in ((self.admit, "admission"), (self.prepare, "training_inputs"),
                                  (self.controls, "holdout_controls")):
            saved = dependency.side_effect
            dependency.side_effect = gate.AdmissionError("rejected")
            try:
                with self.subTest(stage=stage):
                    self.output = self.root / stage
                    self.assertEqual(self.run_entry(), 1)
                    self.assertEqual(self.report("failure.json")["stage"], stage)
                    self.assertFalse((self.output / "success.json").exists())
                    self.runtime.assert_not_called()
            finally:
                dependency.side_effect = saved
        self.prepare.assert_called_with(
            self.verified, processor_receipt=self.root / "processor.json", processor_receipt_sha256="1" * 64,
            prior_schedule=self.root / "prior-schedule.json", static_manifest=self.root / "static-manifest.json",
        )

    def test_source_hash_mismatch_is_rejected_before_admission(self):
        self.source.write_bytes(self.source.read_bytes() + b" ")
        self.assertEqual(self.run_entry(), 1)
        self.assertEqual(self.report("failure.json")["stage"], "source")
        self.admit.assert_not_called()
        self.runtime.assert_not_called()

    def test_source_manifest_cannot_omit_a_runtime_source(self):
        rows = json.loads(self.source.read_bytes())
        self.source.write_bytes(json.dumps(rows[1:]).encode("utf-8"))
        self.source_sha = sha256(self.source.read_bytes()).hexdigest()
        self.assertEqual(self.run_entry(), 1)
        self.assertEqual(self.report("failure.json")["stage"], "source")
        self.admit.assert_not_called()

    def test_local_model_shadow_fails_before_runtime(self):
        model_path = Path(self.verified.frozen_protocol["model"]["id"])
        lstat = Path.lstat

        def shadow(path):
            return NS() if path == model_path else lstat(path)

        with patch.object(Path, "lstat", shadow):
            self.assertEqual(self.run_entry(), 1)
        self.runtime.assert_not_called()
        self.assertEqual(self.report("failure.json")["stage"], "model_path")

    def test_pair_uses_pins_shared_initialization_and_fresh_optimizers(self):
        self.assertEqual(self.run_entry(), 0)
        model_id, revision = (self.verified.frozen_protocol["model"][key] for key in ("id", "revision"))
        self.processor.assert_called_once_with(model_id, revision=revision, token=False, trust_remote_code=False)
        self.assertEqual([c.args for c in self.factory.call_args_list], [(model_id,)] * 3)
        for c in self.factory.call_args_list:
            self.assertEqual(c.kwargs["revision"], revision)
            self.assertFalse(c.kwargs["token"])
            self.assertTrue(c.kwargs["use_safetensors"])
        for model in self.models:
            model.requires_grad_.assert_called_once_with(False)
        self.init.assert_called_once()
        self.assertEqual(self.init.call_args.args[-2], revision)
        self.assertEqual(self.torch.manual_seed.call_args_list, [call(pilot.SEED)] * 5)
        self.assertTrue(all(c.kwargs["is_trainable"] for c in self.wrap.call_args_list))
        self.assertEqual(self.wrap.call_args_list[0].args[1], self.wrap.call_args_list[1].args[1])
        a, b = [c.args for c in self.train.call_args_list]
        self.assertIsNot(a[1], b[1])
        self.assertEqual(a[-1], b[-1])
        self.assertEqual([s.video_ids for s in a[4]], [s.video_ids for s in b[4]])
        self.assertEqual(Counter(i for s in a[4] for i in s.video_ids), {i: 2 for i in a[2]})
        self.assertEqual(a[3], {})
        self.assertTrue(all(s.static_id is None for s in a[4]))
        self.assertTrue(all(s.static_id in b[3] for s in b[4]))
        self.assertEqual([o.step.call_count for o in self.optimizers], [192, 192])
        success = self.report("success.json")
        self.assertEqual(success["status"], "completed_training_pair")
        self.assertFalse(success["model_scoring_started"])
        for arm, count in (("A", 0), ("B", 192)):
            summary = self.report(f"{arm}/summary.json")
            self.assertEqual(summary["static_microbatches"], count)
            self.assertEqual(summary["video_microbatches"], 384)
            self.assertEqual(summary["peak_gpu_allocated_bytes"], 2000)
            self.assertEqual(summary["peak_gpu_reserved_bytes"], 3000)
            checkpoint = self.output / arm / "checkpoint-192"
            self.assertEqual(summary["adapter_sha256"], paired.adapter_digest(checkpoint))
            self.assertTrue((checkpoint / "optimizer.pt").is_file())
        self.assertFalse((self.output / "failure.json").exists())

    def test_unchanged_parameters_fail_without_success(self):
        with patch.object(paired, "parameter_digest", return_value=sha256(b"0").hexdigest()):
            self.assertEqual(self.run_entry(), 1)
        self.assertEqual(self.report("failure.json")["arm"], "A")
        self.assertFalse((self.output / "A" / "summary.json").exists())
        self.assertFalse((self.output / "success.json").exists())

    def test_partial_failure_and_cancellation_preserve_completed_arm(self):
        for error, code, status in ((RuntimeError("private details"), 1, "failed"), (KeyboardInterrupt(), 130, "cancelled")):
            with self.subTest(status=status):
                self.output = self.root / status
                self.optimizers.clear()

                def fail_update(arm_number, updates):
                    if arm_number == 2 and updates == 11:
                        raise error

                self.update_hook.side_effect = fail_update
                self.assertEqual(self.run_entry(), code)
                self.assertEqual(self.report("A/summary.json")["optimizer_steps"], 192)
                self.assertTrue((self.output / "B" / "checkpoint-010" / "receipt.json").exists())
                report = self.report("failure.json")
                self.assertEqual(report["completed_arms"], ["A"])
                self.assertEqual(report["arm"], "B")
                self.assertEqual(report["stage"], "training")
                self.assertEqual(report["status"], status)
                self.assertNotIn("private details", json.dumps(report))
                self.assertFalse((self.output / "success.json").exists())
                original = (self.output / "failure.json").read_bytes()
                self.assertEqual(self.run_entry(), 1)
                self.assertEqual((self.output / "failure.json").read_bytes(), original)


class Loss:
    def detach(self):
        return self

    def item(self):
        return 0.25

    def __mul__(self, weight):
        return self

    def backward(self):
        pass


if __name__ == "__main__":
    unittest.main()

