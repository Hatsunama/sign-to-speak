"""Check that Git excludes common signing keys and credential env files."""

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
KEY_EXTENSIONS = ("keystore", "jks", "p12", "pfx", "key", "pem")
SECRET_PATHS = [
    f"release-signing.{extension}"
    for extension in KEY_EXTENSIONS
] + [
    f"nested/release-signing.{extension.upper()}"
    for extension in KEY_EXTENSIONS
] + [
    ".env.production",
    "nested/.env.production",
    "credentials.env",
    "nested/credentials-prod.env",
]
ORDINARY_PATHS = (
    "src/main.ts",
    "nested/signing-report.md",
    "qa-artifacts/rebuild-local-speech-20261024/report.json",
)


def is_ignored(path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", "--", path],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"git check-ignore failed for {path!r} "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.returncode == 0


def main() -> int:
    failures = []
    for path in SECRET_PATHS:
        if not is_ignored(path):
            failures.append(f"should be ignored: {path}")
    for path in ORDINARY_PATHS:
        if is_ignored(path):
            failures.append(f"should remain visible: {path}")
    if failures:
        print("Key exclusion regression FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print(f"Key exclusion regression passed ({len(SECRET_PATHS)} excluded, "
          f"{len(ORDINARY_PATHS)} visible).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
