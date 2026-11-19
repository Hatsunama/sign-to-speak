"""Synthetic FSboard fit receipts exercise the cloud pair launch gate."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import fsboard_evaluate as scorer
import fsboard_task_cloud_training as cloud
import fsboard_task_fit as fit
import fsboard_v4_pair_launch as launch


class PairLaunchTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.records = self.root / "fit" / "records"
        self.records.mkdir(parents=True)
        self.fit_exit = self.records.parent / "child-exit.json"
        self.source_manifest = self.root / "SOURCE-MANIFEST.json"
        self.processor = self.root / "processor-inputs.json"
        self.control_plan = self.root / "control-plan.json"
        self.pair_output = self.root / "pair"
        sources = []
        for name in sorted(fit.RUNTIME_SOURCES):
            content = Path(cloud.__file__).with_name(name).read_bytes()
            sources.append({"file": name, "sha256": sha256(content).hexdigest(), "bytes": len(content)})
        manifest_sha = self.write_json(self.source_manifest, sources)
        patcher = patch.object(launch, "SOURCE_MANIFEST_SHA256", manifest_sha)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.processor_sha = self.write_json(self.processor, {"status": "INPUTS_VERIFIED"})
        self.control_sha = self.write_json(self.control_plan, {"synthetic": True})
        self.pins = {
            "fit_protocol_sha256": "714523f181dc6a01b6b4666bc7da62483bb18504dc822c5d39ac13ae10a384e9",
            "source_manifest_sha256": manifest_sha,
            "admission_sha256": "780c78b8b3b9f43aa4919815394784f8b5d3cb1432574e87644f39ed4c03d075",
            "proposal_sha256": "6f04da3b218f664d8ab4097ab0212fdd135220f89dc8019e9b3b1fb04213e6d9",
            "protocol_sha256": "9e482b474b1250422e571179ee217a6bf2ad2a223ec28b8fc2f9a288b88a90f1",
            "previous_receipt_sha256": "5be4dd8a5d5225c8131eab855f521a83382a4da3f18804170e917ff381ebd8ac",
            "processor_receipt_sha256": self.processor_sha,
            "prior_schedule_sha256": "6b1980c513f627f3b4bad9cec92c6cda951f0e1fe77fececeb0b319d3f8f27fe",
            "static_manifest_sha256": "55d6034693b3118c4e7fc87f6191effd831410d22d9cb96225c35eb7dc1b0d2e",
            "scorer_sha256": "d3899f88c2c59280b58e4861321d433e8883d1c626445b8ebd53f32411d7c266",
        }
        self.sources = {row["file"]: row["sha256"] for row in sources}
        self.ids = [f"training-{i:03d}" for i in range(1, 7)]
        self.checkpoints = [self.checkpoint(0, 0, 4.0), self.checkpoint(50, 6, 3.0)]

    def write_json(self, path, document):
        payload = (json.dumps(document, indent=2) + "\n").encode("utf-8")
        path.write_bytes(payload)
        return sha256(payload).hexdigest()

    def checkpoint(self, update, matches, nll):
        eos = [248046, 248044]
        rows = []
        for index, (clip_id, reference) in enumerate(zip(self.ids, ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot"))):
            decoded = reference if index < matches else ""
            ids = [100 + index, eos[0]] if decoded else [eos[1]]
            rows.append({
                "clip_id": clip_id, "decoded": decoded, "reference": reference,
                "generated_token_ids": ids, "generated_tokens": len(ids), "stop": "eos",
                "eos_token_ids": eos, "scores": scorer.score(decoded, reference),
                "output_equals_matched": True,
                "model_input": {"supervised_tokens_including_eos": 2, "eos_token_id": eos[0]},
                "reference_nll": {"mean_per_token": nll, "tokens_including_eos": 2, "total": 2 * nll},
            })
        return {
            "optimizer_updates": update, "parameter_sha256": sha256(str(update).encode()).hexdigest(),
            "examples": rows, "eos_token_ids": eos, "aggregate": scorer.aggregate(rows),
            "mean_matched_teacher_nll": nll, "relative_teacher_nll_reduction": 1 - nll / 4 if update else None,
            "fit_guard_passed": update > 0 and matches == 6 and nll <= 3,
        }

    def fixture(self, success_changes=None, exit_changes=None):
        initial, final = self.checkpoints[0], self.checkpoints[-1]
        update = final["optimizer_updates"]
        success = deepcopy({
            "schema_version": 1, "fit_id": fit.FIT_ID, "status": "fit_guard_passed", "scope": fit.FIT_POLICY["scope"],
            "pins": self.pins, "source_sha256": self.sources, "training_clip_ids": self.ids,
            "initial_parameter_sha256": initial["parameter_sha256"], "final_parameter_sha256": final["parameter_sha256"],
            "optimizer_updates": update, "eos_token_ids": final["eos_token_ids"],
            "checkpoints": [{k: v for k, v in row.items() if k != "examples"} for row in self.checkpoints],
            "video_microbatches": update * 2, "static_microbatches": 0, "fit_adapter_reusable": False,
            "holdout_or_development_model_calls": 0, "peak_gpu_allocated_bytes": 100,
            "peak_gpu_reserved_bytes": 200, "elapsed_seconds": 123.45,
        })
        if success_changes:
            success.update(success_changes)
        hashes = {
            f"checkpoint-{row['optimizer_updates']:03d}.json": self.write_json(
                self.records / f"checkpoint-{row['optimizer_updates']:03d}.json", row,
            ) for row in self.checkpoints
        }
        hashes["success.json"] = self.write_json(self.records / "success.json", success)
        exit_document = {"returncode": 0, "saved_json_sha256": hashes, "fit_adapter_reused": False}
        if exit_changes:
            exit_document.update(exit_changes)
        exit_sha = self.write_json(self.fit_exit, exit_document)
        return launch.parse_args([
            "--fit-records", str(self.records), "--fit-child-exit", str(self.fit_exit),
            "--fit-child-exit-sha256", exit_sha, "--fit-success-sha256", hashes["success.json"],
            "--source-manifest", str(self.source_manifest),
            "--admission", str(self.root / "admission.json"), "--proposal", str(self.root / "private-proposal.json"),
            "--previous-receipt", str(self.root / "prior-receipt.json"),
            "--processor-receipt", str(self.processor), "--processor-receipt-sha256", self.processor_sha,
            "--control-plan", str(self.control_plan), "--control-plan-sha256", self.control_sha,
            "--prior-schedule", str(self.root / "prior-schedule.json"),
            "--static-manifest", str(self.root / "static-manifest.json"), "--output", str(self.pair_output),
        ])

    def test_first_passing_checkpoint_launches_only_existing_pair_entrypoint(self):
        args = self.fixture()
        command = launch.build_pair_argv(args)
        self.assertEqual(command[:4], [sys.executable, "-B", "-m", "fsboard_task_cloud_training"])
        flags = dict(zip(command[4::2], command[5::2], strict=True))
        self.assertEqual(flags["--output"], str(self.pair_output))
        self.assertEqual(flags["--source-manifest-sha256"], self.pins["source_manifest_sha256"])
        self.assertEqual(flags["--admission-sha256"], self.pins["admission_sha256"])
        self.assertEqual(flags["--proposal-sha256"], self.pins["proposal_sha256"])
        self.assertEqual(flags["--protocol-sha256"], self.pins["protocol_sha256"])
        self.assertEqual(flags["--processor-receipt-sha256"], self.processor_sha)
        self.assertEqual(flags["--control-plan-sha256"], self.control_sha)
        self.assertFalse(self.pair_output.exists())
        self.assertFalse(any(str(self.records.parent) in value for value in command))

    def test_accepts_100_only_if_50_did_not_pass(self):
        self.checkpoints[1:] = [self.checkpoint(50, 5, 2.0), self.checkpoint(100, 6, 2.0)]
        launch.build_pair_argv(self.fixture())
        self.checkpoints[1] = self.checkpoint(50, 6, 2.0)
        with self.assertRaises(launch.LaunchError):
            launch.build_pair_argv(self.fixture())

    def test_success_and_checkpoint_require_external_byte_pins(self):
        args = self.fixture()
        args.fit_success_sha256 = "0" * 64
        with self.assertRaises(launch.LaunchError):
            launch.build_pair_argv(args)
        args = self.fixture()
        checkpoint = self.records / "checkpoint-050.json"
        checkpoint.write_bytes(checkpoint.read_bytes() + b" ")
        with self.assertRaises(launch.LaunchError):
            launch.build_pair_argv(args)
        checkpoint.unlink()
        with self.assertRaises(launch.LaunchError):
            launch.build_pair_argv(args)

    def test_failed_or_reused_fit_is_rejected(self):
        for changes in ({"returncode": 1}, {"returncode": False}, {"fit_adapter_reused": 0}, {"fit_adapter_reused": True}):
            with self.subTest(changes=changes), self.assertRaises(launch.LaunchError):
                launch.build_pair_argv(self.fixture(exit_changes=changes))
        args = self.fixture()
        (self.records / "failure.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(launch.LaunchError):
            launch.build_pair_argv(args)

    def test_success_fields_are_verified_even_if_bytes_match(self):
        for changes in (
            {"status": "failed"}, {"fit_adapter_reusable": True}, {"fit_adapter_reusable": 0},
            {"holdout_or_development_model_calls": 1}, {"holdout_or_development_model_calls": False},
            {"video_microbatches": 99}, {"static_microbatches": False},
            {"optimizer_updates": 100}, {"eos_token_ids": [248044, 248046]},
            {"pins": {**self.pins, "fit_protocol_sha256": "0" * 64}},
            {"pins": {**self.pins, "admission_sha256": "0" * 64}},
            {"pins": {**self.pins, "processor_receipt_sha256": "0" * 64}},
            {"source_sha256": {**self.sources, "fsboard_task_fit.py": "0" * 64}},
            {"training_clip_ids": self.ids[:5] + ["holdout-001"]},
        ):
            with self.subTest(changes=changes), self.assertRaises(launch.LaunchError):
                launch.build_pair_argv(self.fixture(success_changes=changes))

    def test_forged_checkpoint_pass_fails_on_actual_counts_nll_or_eos(self):
        for matches, nll in ((5, 2.0), (6, 3.01), (6, 2.0)):
            checkpoint = self.checkpoint(50, matches, nll)
            checkpoint["fit_guard_passed"] = True
            checkpoint["aggregate"]["exact_matches"] = 6
            checkpoint["relative_teacher_nll_reduction"] = 0.5
            if matches == 6 and nll == 2.0:
                checkpoint["examples"][0]["generated_token_ids"] = [248046, 248044]
            self.checkpoints[1] = checkpoint
            with self.subTest(matches=matches, nll=nll), self.assertRaises(launch.LaunchError):
                launch.build_pair_argv(self.fixture())

    def test_pair_output_must_be_fresh_and_separate_from_fit(self):
        args = self.fixture()
        self.pair_output.mkdir()
        with self.assertRaises(launch.LaunchError):
            launch.build_pair_argv(args)
        args.output = self.records.parent / "new-pair"
        with self.assertRaises(launch.LaunchError):
            launch.build_pair_argv(args)


if __name__ == "__main__":
    unittest.main()

