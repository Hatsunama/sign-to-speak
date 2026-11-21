import re
from pathlib import Path
import subprocess
import tempfile
import unittest


NATIVE = Path(__file__).resolve().parents[1]
ROOT = NATIVE.parent


class ReplyAppAssetsPolicyTest(unittest.TestCase):
    APP_WORKFLOWS = (
        "sign-android.yml",
        "sign-emulator-build.yml",
        "sign-roboflow-trackers.yml",
    )

    def test_gradle_applies_fail_closed_reply_asset_contract(self):
        build = (NATIVE / "android/app/build.gradle").read_text(encoding="utf-8")
        self.assertIn('apply from: "reply-asr-assets.gradle"', build)
        policy_path = NATIVE / "android/app/reply-asr-assets.gradle"
        self.assertTrue(policy_path.is_file())
        policy = policy_path.read_text(encoding="utf-8")
        for required in (
            'generated/reply-asr-component',
            'android.sourceSets.main.assets.srcDir(replyAssets)',
            'noCompress "bin"',
            'verifyReplyAsrInputs',
            'ggml-tiny.en.bin',
            '77704715L',
            '921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f',
            'ggml-silero-v6.2.0.bin',
            '885098L',
            '2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987',
            'java.nio.file.Files.isSymbolicLink',
            'MessageDigest.getInstance("SHA-256")',
            'reply-asr-notices',
        ):
            self.assertIn(required, policy)
        compact = re.sub(r"\s+", " ", policy)
        self.assertRegex(compact, r'tasks\.matching.*merge.*Assets.*dependsOn\(verifyReplyAsrInputs\)')
        self.assertRegex(compact, r'tasks\.matching.*package.*dependsOn\(verifyReplyAsrInputs\)')

    def test_gradle_preserves_porcelain_leading_status_column(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            subprocess.run(["git", "init", "--quiet"], cwd=checkout, check=True)
            subprocess.run(
                ["git", "config", "user.email", "reply-asr-fixture@example.invalid"],
                cwd=checkout,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Reply ASR fixture"],
                cwd=checkout,
                check=True,
            )
            for relative in ("include/whisper.h", "src/whisper.cpp"):
                path = checkout / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("pinned\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=checkout, check=True)
            subprocess.run(
                ["git", "commit", "--quiet", "-m", "fixture"], cwd=checkout, check=True
            )
            for relative in ("include/whisper.h", "src/whisper.cpp"):
                (checkout / relative).write_text("repaired\n", encoding="utf-8")

            porcelain = subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=checkout,
                text=True,
            )

        expected = {" M include/whisper.h", " M src/whisper.cpp"}
        self.assertEqual(set(porcelain.rstrip("\r\n").splitlines()), expected)
        self.assertNotEqual(set(porcelain.strip().splitlines()), expected)

        policy = (NATIVE / "android/app/reply-asr-assets.gradle").read_text(encoding="utf-8")
        self.assertIn('.replaceFirst(/\\r?\\n\\z/, "")', policy)
        self.assertNotIn(".standardOutput.asText.get().trim()", policy)

    def test_hosted_prepare_precedes_every_gradle_invocation(self):
        workflow = (ROOT / ".github/workflows/sign-android.yml").read_text(encoding="utf-8")
        prepare = workflow.index("Prepare pinned Reply ASR app inputs")
        command = workflow.index(
            "python3 scripts/reply-asr-component.py prepare generated/reply-asr-component")
        first_gradle = workflow.index("./gradlew")
        self.assertLess(prepare, command)
        self.assertLess(command, first_gradle)
        self.assertIn("scripts/reply-asr-component.py", workflow[:first_gradle])
        self.assertIn("vendor/reply-asr/pins.json", workflow[:first_gradle])

    def test_every_app_prepare_creates_its_checkout_owned_parent(self):
        guarded_prepare = (
            "mkdir -p generated\n"
            "          python3 scripts/reply-asr-component.py prepare "
            "generated/reply-asr-component"
        )
        for name in self.APP_WORKFLOWS:
            with self.subTest(workflow=name):
                workflow = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
                self.assertEqual(workflow.count(guarded_prepare), 1)

    def test_app_integration_pr_branch_is_narrowly_allowlisted(self):
        workflow = (ROOT / ".github/workflows/sign-android.yml").read_text(encoding="utf-8")
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", workflow)
        self.assertIn("github.head_ref == 'fix/android-native-foundation'", workflow)
        self.assertIn("github.head_ref == 'codex/sign-to-speak-reply-app-20261121'", workflow)

    def test_final_apk_gate_requires_exact_bundled_notice_inventory(self):
        gate = (NATIVE / "scripts/check-reply-apk-integration.py").read_text(encoding="utf-8")
        self.assertIn("NOTICE_ASSETS", gate)
        self.assertIn('assets/reply-asr-notices/', gate)
        for name in (
            "WHISPER-CPP-MIT.txt", "OPENAI-WHISPER-MIT.txt", "OPENAI-GPT2-LICENSE.txt",
            "ARM-OPTIMIZED-ROUTINES-LICENSE.txt", "FP16-MIT.txt", "YARN-MIT.txt",
            "SILERO-MIT.txt", "ARM-EXP-COPYRIGHT.txt", "ATTRIBUTION.txt",
        ):
            self.assertIn(name, gate)


if __name__ == "__main__":
    unittest.main()
