"""Exercise worker outcomes with real child processes and isolated local files."""

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from tempfile import TemporaryDirectory
import unittest

from holistic_probe_runner import run_worker


RECEIPT = b'{"schema_version":1,"status":"SUCCESS","frame_counts":{"processed":1}}\n'
FAILURE = b'{"schema_version":1,"status":"FAILURE","error_type":"ValueError"}\n'


class WorkerRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.results = self.root / "results"
        self.durable = self.root / "durable"
        self.durable.mkdir()
        self.script = self.root / "worker.py"
        self.env = {"SystemRoot": os.environ["SystemRoot"]} if os.name == "nt" else {}

    def run_child(self, body, timeout=10):
        self.script.write_text(
            "import os, sys, time\n"
            "from pathlib import Path\n"
            "result = Path(sys.argv[1])\n"
            "result.mkdir()\n"
            "def publish(name, payload):\n"
            "    staged = result / (name + '.tmp')\n"
            "    staged.write_bytes(payload)\n"
            "    staged.rename(result / name)\n"
            "sys.stdout.buffer.write(b'worker stdout\\n')\n"
            "sys.stdout.buffer.flush()\n"
            "sys.stderr.buffer.write(b'worker stderr\\n')\n"
            "sys.stderr.buffer.flush()\n"
            + body,
            encoding="utf-8",
        )
        return run_worker(
            [sys.executable, "-B", str(self.script), str(self.results)],
            cwd=self.root, env=self.env, result_dir=self.results,
            durable_dir=self.durable, timeout=timeout,
        )

    def assert_outcome(self, outcome, status, returncode):
        self.assertEqual(outcome, json.loads((self.durable / "worker-exit.json").read_bytes()))
        self.assertEqual(outcome["status"], status)
        self.assertEqual(outcome["returncode"], returncode)
        self.assertGreater(outcome["wall_seconds"], 0)
        self.assertEqual((self.durable / "worker.stdout").read_bytes(), b"worker stdout\n")
        self.assertEqual((self.durable / "worker.stderr").read_bytes(), b"worker stderr\n")
        if os.name == "posix":
            for path in self.durable.iterdir():
                if path.name.startswith("worker"):
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def assert_artifact(self, outcome, name, payload):
        durable_name = "worker-" + name
        self.assertEqual(outcome["artifacts"][durable_name], {
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
        self.assertEqual((self.durable / durable_name).read_bytes(), payload)
        self.assertEqual((self.results / name).read_bytes(), payload)

    def test_ordinary_exit_binds_the_receipt(self):
        outcome = self.run_child(
            f"publish('receipt.json', {RECEIPT!r})\n"
            "assert sys.stdin.buffer.read() == b''\n"
        )

        self.assert_outcome(outcome, "exited", 0)
        self.assert_artifact(outcome, "receipt.json", RECEIPT)
        self.assertEqual(outcome["capture_errors"], {})

    def test_nonzero_exit_preserves_failure_diagnostics(self):
        outcome = self.run_child(f"publish('failure.json', {FAILURE!r})\nsys.exit(13)\n")

        self.assert_outcome(outcome, "exited", 13)
        self.assert_artifact(outcome, "failure.json", FAILURE)
        self.assertEqual(outcome["capture_errors"], {})

    def test_timeout_before_publication_ignores_uncommitted_receipt(self):
        outcome = self.run_child(
            "(result / 'receipt.json.tmp').write_bytes(b'{')\ntime.sleep(30)\n",
            timeout=1,
        )

        self.assert_outcome(outcome, "timed_out", None)
        self.assertEqual(outcome["artifacts"], {})
        self.assertEqual(outcome["capture_errors"], {})
        self.assertFalse((self.durable / "worker-receipt.json").exists())
        self.assertEqual((self.results / "receipt.json.tmp").read_bytes(), b"{")

    def test_timeout_after_publication_preserves_success_receipt_as_evidence(self):
        outcome = self.run_child(
            f"publish('receipt.json', {RECEIPT!r})\ntime.sleep(30)\n",
            timeout=1,
        )

        self.assert_outcome(outcome, "timed_out", None)
        self.assert_artifact(outcome, "receipt.json", RECEIPT)
        self.assertEqual(outcome["capture_errors"], {})

    def test_capture_collision_keeps_worker_error_and_later_failure_receipt(self):
        destination = self.durable / "worker-receipt.json"
        destination.touch(mode=0o600)
        destination.write_bytes(b"preexisting evidence")
        outcome = self.run_child(
            f"publish('receipt.json', {RECEIPT!r})\n"
            f"publish('failure.json', {FAILURE!r})\n"
            "sys.exit(13)\n",
        )

        self.assert_outcome(outcome, "exited", 13)
        self.assertEqual(destination.read_bytes(), b"preexisting evidence")
        self.assertNotIn(destination.name, outcome["artifacts"])
        self.assertEqual(outcome["capture_errors"], {destination.name: "FileExistsError"})
        self.assert_artifact(outcome, "failure.json", FAILURE)

    def test_stream_collision_stops_launch_and_preserves_file(self):
        destination = self.durable / "worker.stderr"
        destination.write_bytes(b"preexisting diagnostics")
        outcome = self.run_child("raise AssertionError('worker must not launch')\n")

        self.assertEqual(outcome["status"], "launch_failed")
        self.assertIsNone(outcome["returncode"])
        self.assertEqual(outcome["error_type"], "FileExistsError")
        self.assertEqual(destination.read_bytes(), b"preexisting diagnostics")
        self.assertFalse(self.results.exists())
        self.assertEqual(outcome, json.loads((self.durable / "worker-exit.json").read_bytes()))

    def test_exit_marker_collision_stops_launch_and_preserves_file(self):
        destination = self.durable / "worker-exit.json"
        destination.write_bytes(b"preexisting outcome")

        with self.assertRaises(FileExistsError):
            self.run_child("raise AssertionError('worker must not launch')\n")

        self.assertEqual(destination.read_bytes(), b"preexisting outcome")
        self.assertFalse(self.results.exists())
        self.assertFalse((self.durable / "worker.stdout").exists())

    def test_unstartable_worker_persists_launch_failure(self):
        outcome = run_worker(
            [str(self.root / "absent-executable")],
            cwd=self.root, env=self.env, result_dir=self.results,
            durable_dir=self.durable, timeout=1,
        )

        self.assertEqual(outcome["status"], "launch_failed")
        self.assertIsNone(outcome["returncode"])
        self.assertEqual(outcome["error_type"], "FileNotFoundError")
        self.assertEqual(outcome["artifacts"], {})
        self.assertEqual(outcome, json.loads((self.durable / "worker-exit.json").read_bytes()))


if __name__ == "__main__":
    unittest.main()
