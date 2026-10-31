"""Static dependency alignment check; this does not compile or exercise Rust."""

from pathlib import Path
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]


class XnPinTest(unittest.TestCase):
    def test_exact_xn_pin_matches_verified_upstream_lock(self):
        manifest = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))
        source = tomllib.loads(
            (Path(__file__).parent / "fixtures/upstream-xn-lock-entry.toml").read_text(encoding="utf-8")
        )
        upstream_xn = next(p for p in source["package"] if p["name"] == "xn")
        self.assertEqual(manifest["dependencies"]["xn"], "=" + upstream_xn["version"])


if __name__ == "__main__":
    unittest.main()
