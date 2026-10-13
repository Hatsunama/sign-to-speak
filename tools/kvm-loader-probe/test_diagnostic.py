import unittest
import diagnostic as d
class Diagnostics(unittest.TestCase):
    def test_classify_only_library_and_version_identifiers(self):
        value=d.classify("/private/path/emulator: error while loading shared libraries: libpulse.so.0: cannot open shared object file\nlibfoo.so.3 => not found\nversion `GLIBC_2.40' not found\nsecret-token-example")
        self.assertEqual(value, {'missing_libraries':['libfoo.so.3','libpulse.so.0'], 'missing_versions':['GLIBC_2.40']})
    def test_no_loader_error_is_not_invented(self):
        self.assertEqual(d.classify('unknown error exit127'), {'missing_libraries':[], 'missing_versions':[]})
    def test_diagnostic_never_opens_device(self):
        self.assertEqual(d.NoDeviceLease().acquire(), {'mode':'diagnostic-only-no-kvm-access'})
        self.assertIsNone(d.NoDeviceLease().release())
    def test_wrong_public_context_rejected(self):
        env=dict(PROBE_REPOSITORY_PRIVATE='false', PROBE_REPOSITORY_VISIBILITY='public',GITHUB_REPOSITORY='Hatsunama/sign-to-speak',GITHUB_ACTIONS='true',RUNNER_ENVIRONMENT='github-hosted',GITHUB_EVENT_NAME='push',GITHUB_REF=d.BRANCH,ImageOS='ubuntu24',RUNNER_OS='Linux',RUNNER_ARCH='X64',PROBE_COMMIT='a'*40)
        d.policy(env)
        for key in env:
            bad=dict(env);bad[key]='unexpected'
            with self.subTest(key=key),self.assertRaises(d.probe.Blocked): d.policy(bad)
if __name__=='__main__':unittest.main(verbosity=2)
