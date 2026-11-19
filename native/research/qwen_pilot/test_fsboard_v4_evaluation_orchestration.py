"""Offline tests for the evaluation job's child/result contract."""
import ast
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import runpy
import signal
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fsboard_v4_evaluation_launch import BindingError, EXPERIMENT_ID


TEMPLATE = Path(__file__).with_name("fsboard-v4-evaluate-one-mount-body-template-r1.py.txt")
CONDITIONS = ("matched", "reordered", "repeated_middle_frame", "black", "wrong_video")


class OrchestrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        previous_umask = os.umask(0o077)
        try:
            with patch.dict(os.environ, {}, clear=False):
                cls.body = runpy.run_path(str(TEMPLATE))
        finally:
            os.umask(previous_umask)
        cls.evaluator = SimpleNamespace(
            CONDITIONS=CONDITIONS, PINS=("admission_sha256", "proposal_sha256", "protocol_sha256",
                                        "previous_receipt_sha256"),
            protocol=SimpleNamespace(SCORER_SHA256="s"),
            _summarize=lambda scores: {"conditions": {condition: len(rows) for condition, rows in scores.items()}},
        )
        cls.candidates = {name: {"candidate": name, "model": "mock"} for name in ("base", "A", "B")}
        cls.clips = [{"id": f"clip-{index:03}", "reference": "reference", "signer_id": "s", "split": "train"}
                     for index in range(64)]
        cls.plan = {"examples": {clip["id"]: {"conditions": {condition: {"option": condition} for condition in CONDITIONS}}
                                 for clip in cls.clips}}
        cls.encoded = {"supervised_tokens_including_eos": 2}
        cls.controls = SimpleNamespace(
            payload=json.dumps({"examples": {clip["id"]: {condition: cls.encoded for condition in CONDITIONS}
                                              for clip in cls.clips}}).encode(),
            admission_binding=("a", "b", "c", "d"), plan_file_sha256="plan-file", plan_sha256="plan-content",
            sha256="controls",
        )

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name).resolve()
        self.output = self.work / "local"
        self.out = self.work / "remote"
        self.out.mkdir()

    def write_local(self, relative, content):
        path = self.output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((json.dumps(content, indent=2) + "\n").encode("utf-8"))

    def populate(self, *, missing=None, mixed_candidate=None):
        self.write_local("runtime_receipt.json", {"device": "synthetic"})
        for candidate, binding in self.candidates.items():
            self.write_local(f"bindings/{candidate}.json", binding)
            for clip in self.clips:
                for condition in CONDITIONS:
                    name = f"{candidate}/{clip['id']}/{condition}.json"
                    if name == missing:
                        continue
                    self.write_local(name, {
                        "candidate": "base" if name == mixed_candidate else candidate, "condition": condition,
                        "clip_id": clip["id"], "signer_id": clip["signer_id"], "publisher_split": clip["split"],
                        "reference": clip["reference"],
                        "control": self.plan["examples"][clip["id"]]["conditions"][condition],
                        "model_input": self.encoded, "reference_nll": {"tokens_including_eos": 2},
                    })
            self.write_local(f"{candidate}/summary.json", {
                "candidate": candidate, "status": "HOLDOUT_EVALUATED", "experiment_id": EXPERIMENT_ID,
                "pins": {
                    **dict(zip(self.evaluator.PINS, self.controls.admission_binding, strict=True)),
                    "control_plan_sha256": self.controls.plan_file_sha256,
                    "control_plan_content_sha256": self.controls.plan_sha256,
                    "control_inputs_sha256": self.controls.sha256,
                    "scorer_sha256": self.evaluator.protocol.SCORER_SHA256,
                },
                "conditions": {name: 64 for name in CONDITIONS},
            })

    def finish(self, *, child_returncode=0, cancel=False):
        def launch(*args, **kwargs):
            if cancel:
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            return SimpleNamespace(poll=lambda: child_returncode, returncode=child_returncode)
        with patch.dict(sys.modules, {"fsboard_task_evaluate": self.evaluator}), patch("subprocess.Popen", launch):
            result = self.body["run_and_capture"]([], self.work, {}, self.output, self.out, self.candidates,
                                                  self.clips, self.plan, self.controls)
        return result, json.loads((self.out / "child-exit.json").read_bytes())

    def test_parent_and_child_parse_and_runtime_environment_is_least_privilege(self):
        ast.parse(TEMPLATE.read_text(encoding="utf-8"))
        ast.parse(self.body["INPUT_LOADER_SOURCE"] + self.body["CHILD_BODY"])
        with patch.dict(os.environ, {"HF_TOKEN": "test-token", "PYTHONOPTIMIZE": "1", "HOME": "outside",
                                     "OPENBLAS_NUM_THREADS": "24", "PYTHONPATH": "outside"}, clear=False):
            env = self.body["runtime_environment"](self.work)
        peak_threads = {key: env[key] for key in
                        ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS")}
        self.assertEqual(set(peak_threads.values()), {"1"})
        self.assertFalse(set(env) & {"HF_TOKEN", "HOME", "PYTHONOPTIMIZE", "PYTHONPATH"})
        self.assertEqual(env["PYTORCH_KERNEL_CACHE_PATH"], str(self.work / "torch-kernel-cache"))

    def test_template_pins_the_exact_support_source(self):
        support = TEMPLATE.with_name(self.body["SUPPORT_NAME"]).read_bytes()
        self.assertEqual(self.body["SUPPORT_BYTES"], len(support))
        self.assertEqual(self.body["SUPPORT_SHA"], sha256(support).hexdigest())

    def test_live_poll_defers_direct_runtime_write_until_complete(self):
        relative = "runtime_receipt.json"
        self.output.mkdir()
        copied = {}
        capture = self.body["capture_records"]
        payload = b'{\n  "device": "synthetic"\n}\n'
        with (self.output / relative).open("xb", buffering=0) as stream:
            position = 0
            for boundary in (0, 14, len(payload) - 2, len(payload) - 1):
                stream.write(payload[position:boundary])
                position = boundary
                with self.subTest(published_bytes=boundary):
                    capture(self.output, self.out, {relative}, copied)
                    self.assertEqual(copied, {})
            stream.write(payload[-1:])
            capture(self.output, self.out, {relative}, copied)
        capture(self.output, self.out, {relative}, copied, strict=True)
        self.assertEqual((self.out / relative).read_bytes(), payload)
        self.assertEqual(copied, {relative: {"sha256": sha256(payload).hexdigest(), "bytes": len(payload)}})

    def test_strict_capture_rejects_incomplete_or_invalid_runtime_receipt(self):
        relative = "runtime_receipt.json"
        self.output.mkdir()
        for payload in (b"", b'{\n  "device":', b'{\n  "device": "synthetic"\n}', b'{\n  invalid\n}\n'):
            with self.subTest(payload=payload):
                (self.output / relative).write_bytes(payload)
                with self.assertRaisesRegex((BindingError, RuntimeError), "runtime receipt|runtime_receipt.json"):
                    self.body["capture_records"](self.output, self.out, {relative}, {}, strict=True)

    def test_live_capture_rejects_malformed_condition_json(self):
        relative = "A/clip-000/matched.json"
        payload = self.output / relative
        payload.parent.mkdir(parents=True)
        payload.write_bytes(b"{invalid}")
        with self.assertRaisesRegex(BindingError, "matched.json"):
            self.body["capture_records"](self.output, self.out, {relative}, {})

    def test_parent_attests_complete_row_counts_and_binding(self):
        self.populate()
        code, report = self.finish()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "completed_holdout_evaluation")
        self.assertEqual(report["scored_records"], 960)
        self.assertEqual(len(report["saved_artifacts"]), 967)
        self.assertEqual({row["scored_records"] for row in report["candidates"].values()}, {320})

    def test_zero_child_exit_with_missing_record_preserves_partial_results(self):
        self.populate(missing="B/clip-063/wrong_video.json")
        code, report = self.finish()
        self.assertEqual(code, 1)
        self.assertEqual(report["returncode"], 0)
        self.assertEqual(report["status"], "evaluation_incomplete")
        self.assertEqual(len(report["saved_artifacts"]), 966)
        self.assertNotIn("scored_records", report)

    def test_mislabeled_score_fails_even_with_complete_file_counts(self):
        self.populate(mixed_candidate="A/clip-032/black.json")
        code, report = self.finish()
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "evaluation_incomplete")
        self.assertEqual(len(report["saved_artifacts"]), 967)
        self.assertIn("disagrees", report["orchestration_error_detail"])

    def test_signal_preserves_completed_artifacts_and_child_failure(self):
        self.write_local("base/clip-000/matched.json", {"candidate": "base"})
        self.write_local("failure.json", {"stage": "holdout", "error_type": "KeyboardInterrupt"})
        code, report = self.finish(child_returncode=130, cancel=True)
        self.assertEqual(code, 130)
        self.assertTrue(report["cancellation_requested"])
        self.assertEqual(report["status"], "evaluation_incomplete")
        self.assertEqual(report["saved_artifacts"].keys(), {"base/clip-000/matched.json", "failure.json"})

    def test_final_capture_preserves_records_after_a_malformed_record(self):
        first = "A/clip-000/black.json"
        corrupt = "A/clip-000/matched.json"
        last = "B/clip-063/wrong_video.json"
        self.write_local(first, {"record": "first"})
        (self.output / corrupt).write_bytes(b"{invalid}")
        self.write_local(last, {"record": "last"})

        code, report = self.finish()

        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "evaluation_incomplete")
        self.assertFalse(report["strict_capture_complete"])
        self.assertIn("matched.json", report["orchestration_error_detail"])
        self.assertEqual(report["saved_artifacts"].keys(), {first, last})
        self.assertEqual((self.out / "artifacts" / last).read_bytes(), (self.output / last).read_bytes())
        self.assertFalse((self.out / "artifacts" / corrupt).exists())

    def test_failed_exclusive_write_allows_strict_retry(self):
        relative = "A/clip-000/matched.json"
        self.write_local(relative, {"record": "completed"})
        payload = (self.output / relative).read_bytes()
        destination = self.out / "artifacts" / relative
        open_file = Path.open
        attempted = False

        @contextmanager
        def short_write(path, mode, *args, **kwargs):
            with open_file(path, mode, *args, **kwargs) as stream:
                yield SimpleNamespace(write=lambda data: stream.write(data[:-1]))

        def open_with_short_write(path, mode="r", *args, **kwargs):
            nonlocal attempted
            if path == destination and mode == "xb" and not attempted:
                attempted = True
                return short_write(path, mode, *args, **kwargs)
            return open_file(path, mode, *args, **kwargs)

        polls = iter((None, 0))
        child = SimpleNamespace(poll=lambda: next(polls, 0), returncode=0)
        with (
            patch.dict(sys.modules, {"fsboard_task_evaluate": self.evaluator}),
            patch("subprocess.Popen", return_value=child),
            patch.object(Path, "open", open_with_short_write),
        ):
            code = self.body["run_and_capture"]([], self.work, {}, self.output, self.out, self.candidates,
                                                self.clips, self.plan, self.controls)

        report = json.loads((self.out / "child-exit.json").read_bytes())
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "evaluation_incomplete")
        self.assertTrue(report["strict_capture_complete"])
        self.assertEqual(report["orchestration_error_type"], "RuntimeError")
        self.assertEqual(report["saved_artifacts"][relative], {"sha256": sha256(payload).hexdigest(),
                                                              "bytes": len(payload)})
        self.assertEqual(destination.read_bytes(), payload)

    def test_capture_cleanup_error_preserves_the_readback_error(self):
        relative = "A/clip-000/matched.json"
        self.write_local(relative, {"record": "completed"})
        target = self.out / relative

        with (
            patch.object(Path, "read_bytes", side_effect=OSError("readback unavailable")),
            patch.object(Path, "unlink", autospec=True, side_effect=PermissionError("cleanup unavailable")) as unlink,
            self.assertRaisesRegex(OSError, "readback unavailable") as failure,
        ):
            self.body["capture_records"](self.output, self.out, {relative}, {}, strict=True)

        self.assertEqual(unlink.call_args.args, (target,))
        self.assertIn("cleanup unavailable", failure.exception.__notes__[0])

    def test_strict_retry_never_removes_an_existing_target(self):
        relative = "A/clip-000/matched.json"
        self.write_local(relative, {"record": "completed"})
        target = self.out / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(b"existing bytes")
        copied = {}

        with self.assertRaises(FileExistsError):
            self.body["capture_records"](self.output, self.out, {relative}, copied, strict=True)

        self.assertEqual(copied, {})
        self.assertEqual(target.read_bytes(), b"existing bytes")

    def test_signal_before_terminal_publication_cancels_with_all_artifacts(self):
        self.populate()
        body_globals = self.body["run_and_capture"].__globals__
        verify = body_globals["verify_complete"]

        def verify_and_cancel(*args):
            summaries = verify(*args)
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            return summaries

        with patch.dict(body_globals, {"verify_complete": verify_and_cancel}):
            code, report = self.finish()
        self.assertEqual(code, 130)
        self.assertEqual(report["status"], "evaluation_incomplete")
        self.assertTrue(report["cancellation_requested"])
        self.assertEqual(len(report["saved_artifacts"]), 967)
        self.assertNotIn("scored_records", report)

    def test_signal_during_terminal_publication_preserves_the_committed_outcome(self):
        relative = "A/clip-000/matched.json"
        self.write_local(relative, {"record": "completed"})
        body_globals = self.body["run_and_capture"].__globals__
        write = body_globals["write_json"]

        def write_and_signal(path, report):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            write(path, report)

        with patch.dict(body_globals, {"write_json": write_and_signal}):
            code, report = self.finish(child_returncode=1)
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "evaluation_incomplete")
        self.assertFalse(report["cancellation_requested"])
        self.assertEqual(report["saved_artifacts"].keys(), {relative})

    def run_bootstrap(self, *, cleanup_signal=False, publication_error=False):
        body_globals = self.body["run_and_capture"].__globals__
        write = body_globals["write_json"]
        entry_point = ast.parse(TEMPLATE.read_bytes()).body[-1]
        entry = compile(ast.Module(body=[entry_point], type_ignores=[]), str(TEMPLATE), "exec")
        child = SimpleNamespace(poll=lambda: 0, returncode=0)

        def main(out, work, *args, **kwargs):
            return body_globals["run_and_capture"]([], work, {}, self.output, out, self.candidates,
                                                   self.clips, self.plan, self.controls, *args, **kwargs)

        def write_receipt(path, report):
            if publication_error:
                if path.name == "child-exit.json":
                    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                    raise OSError("terminal receipt write failed")
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            write(path, report)

        @contextmanager
        def temporary_directory(**kwargs):
            yield str(self.work)
            if cleanup_signal:
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

        (self.work / "out").mkdir()
        previous = signal.getsignal(signal.SIGTERM)
        try:
            with (
                patch.dict(body_globals, {"__name__": "__main__", "JOB": self.work,
                                          "main": main, "write_json": write_receipt}),
                patch.dict(sys.modules, {"fsboard_task_evaluate": self.evaluator}),
                patch("subprocess.Popen", return_value=child),
                patch("tempfile.TemporaryDirectory", temporary_directory),
                self.assertRaises(SystemExit) as exit_,
            ):
                exec(entry, body_globals)
        except KeyboardInterrupt:
            self.fail("A late SIGTERM escaped bootstrap terminal handling")
        finally:
            signal.signal(signal.SIGTERM, previous)
        return exit_.exception.code, self.work / "out/run"

    def test_cleanup_signal_cannot_override_published_success(self):
        self.populate()
        code, out = self.run_bootstrap(cleanup_signal=True)
        report = json.loads((out / "child-exit.json").read_bytes())
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "completed_holdout_evaluation")
        self.assertFalse((out / "bootstrap-failure.json").exists())

    def test_signal_during_failed_publication_cannot_mask_the_write_error(self):
        self.populate()
        code, out = self.run_bootstrap(publication_error=True, cleanup_signal=True)
        report = json.loads((out / "bootstrap-failure.json").read_bytes())
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "evaluation_incomplete")
        self.assertEqual(report["error_type"], "OSError")
        self.assertEqual(report["detail"], "terminal receipt write failed")
        self.assertFalse((out / "child-exit.json").exists())
