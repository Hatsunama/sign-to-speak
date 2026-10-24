"""Regression tests for the read-only prepared-source verification mode."""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


FLITE = pathlib.Path(__file__).resolve().parents[2] / 'android/app/src/main/cpp/flite'


class PreparedSourcesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.flite = pathlib.Path(self.temp.name) / 'flite'
        self.flite.mkdir()
        for name in ('prepare_sources.py', 'source-manifest.json',
                     'source-receipt.json', 'sources.cmake', 'modifications.patch'):
            shutil.copy2(FLITE / name, self.flite / name)
        shutil.copytree(FLITE / 'upstream', self.flite / 'upstream')
        shutil.copytree(FLITE / 'generated', self.flite / 'generated')

    def check(self):
        return subprocess.run([sys.executable, str(self.flite / 'prepare_sources.py'), '--check'],
                              capture_output=True, text=True)

    def test_matching_generated_files_are_verified_without_writes(self):
        watched = [p for p in self.flite.rglob('*') if p.is_file()]
        before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in watched}
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in watched})
    def test_modified_generated_source_is_rejected_without_repair(self):
        target = self.flite / 'generated/cst_mlsa.c'
        altered = target.read_bytes() + b'\n/* unintended edit */\n'
        target.write_bytes(altered)
        result = self.check()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('generated/cst_mlsa.c', result.stderr)
        self.assertEqual(target.read_bytes(), altered, 'check must not repair generated files')

    def test_missing_and_extra_generated_files_are_rejected_without_writes(self):
        missing = self.flite / 'generated/cst_mlsa.c'
        missing.unlink()
        extra = self.flite / 'generated/unexpected.c'
        extra.write_bytes(b'int unexpected;\n')
        result = self.check()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('generated/cst_mlsa.c', result.stderr)
        self.assertIn('generated/unexpected.c', result.stderr)
        self.assertFalse(missing.exists())
        self.assertTrue(extra.exists())


if __name__ == '__main__':
    unittest.main()
