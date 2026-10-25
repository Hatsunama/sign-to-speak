import importlib.util
import pathlib
import tempfile
import unittest
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[2]
NOTICE = REPO / 'native/vendor/mediapipe-offline/licenses/sdk-licenses/mediapipe-tasks-1.1.1-NOTICE'
APACHE = REPO / 'native/vendor/mediapipe-offline/licenses/sdk-licenses/Apache-2.0-LICENSE'
LITERT = REPO / 'native/vendor/mediapipe-offline/licenses/sdk-licenses/litert-1.4.2-LICENSE'
LICENSE_ASSETS = {
    'assets/sdk-licenses/mediapipe-tasks-1.1.1-NOTICE': NOTICE,
    'assets/sdk-licenses/Apache-2.0-LICENSE': APACHE,
    'assets/sdk-licenses/litert-1.4.2-LICENSE': LITERT,
}

SCRIPT = pathlib.Path(__file__).with_name('check-phone-instrumentation.py')
spec = importlib.util.spec_from_file_location('phone_instrumentation', SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
QA = 'com.xmilo_at_your_side.sign_to_speak.qa'


class InstrumentationArtifact(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.xml = f'''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{QA}.test"><uses-sdk android:minSdkVersion="26" android:targetSdkVersion="36"/><uses-permission android:name="android.permission.REORDER_TASKS"/><application android:testOnly="true"/><instrumentation android:name="androidx.test.runner.AndroidJUnitRunner" android:targetPackage="{QA}"/></manifest>'''
        self.entries = {'AndroidManifest.xml': b'manifest', 'classes.dex': b'dex'}
        self.entries.update({name: path.read_bytes() for name, path in LICENSE_ASSETS.items()})

    def verify(self):
        manifest = self.root / 'manifest.xml'
        apk = self.root / 'test.apk'
        manifest.write_text(self.xml, encoding='utf-8')
        with zipfile.ZipFile(apk, 'w') as archive:
            for name, data in self.entries.items():
                archive.writestr(name, data)
        audit.verify(manifest, apk)

    def reject_xml(self, old, new):
        self.xml = self.xml.replace(old, new)
        with self.assertRaises(AssertionError):
            self.verify()

    def test_valid(self):
        self.verify()

    def test_missing_sdk_license_assets(self):
        for name in LICENSE_ASSETS:
            with self.subTest(name=name):
                data = self.entries.pop(name)
                try:
                    with self.assertRaises(AssertionError): self.verify()
                finally:
                    self.entries[name] = data

    def test_changed_sdk_license_assets(self):
        for name in LICENSE_ASSETS:
            with self.subTest(name=name):
                original = self.entries[name]
                self.entries[name] = original + b'changed'
                try:
                    with self.assertRaises(AssertionError): self.verify()
                finally:
                    self.entries[name] = original

    def test_arm64_library_allowed(self):
        self.entries['lib/arm64-v8a/libfoo.so'] = b'native'
        self.verify()

    def test_wrong_package(self):
        self.reject_xml(f'{QA}.test', 'other.test')

    def test_wrong_target(self):
        self.reject_xml(f'android:targetPackage="{QA}"', 'android:targetPackage="other"')

    def test_wrong_runner(self):
        self.reject_xml('androidx.test.runner.AndroidJUnitRunner', 'other.Runner')

    def test_duplicate_runner(self):
        self.reject_xml('</manifest>', f'<instrumentation android:name="androidx.test.runner.AndroidJUnitRunner" android:targetPackage="{QA}"/></manifest>')

    def test_wrong_sdk(self):
        self.reject_xml('android:targetSdkVersion="36"', 'android:targetSdkVersion="35"')

    def test_not_test_only(self):
        self.reject_xml('android:testOnly="true"', 'android:testOnly="false"')

    def test_unplanned_application_components(self):
        components = (
            '<service android:name="com.google.mediapipe.tasks.components.DataTransportService" android:exported="false"/>',
            '<receiver android:name="com.google.mediapipe.tasks.components.DataTransportReceiver" android:exported="false"/>',
            '<provider android:name="com.google.mediapipe.tasks.components.DataTransportProvider" android:authorities="test.provider" android:exported="false"/>',
        )
        self.verify()  # The baseline manifest requests only REORDER_TASKS.
        for component in components:
            with self.subTest(component=component):
                self.xml = self.xml.replace('<application android:testOnly="true"/>',
                                            f'<application android:testOnly="true">{component}</application>')
                try:
                    with self.assertRaises(AssertionError):
                        self.verify()
                finally:
                    self.xml = self.xml.replace(f'<application android:testOnly="true">{component}</application>',
                                                '<application android:testOnly="true"/>')

    def test_androidx_test_activity_allowed(self):
        self.xml = self.xml.replace('<application android:testOnly="true"/>',
                                    '<application android:testOnly="true"><activity android:name="androidx.test.core.app.InstrumentationActivityInvoker$BootstrapActivity"/></application>')
        self.verify()

    def test_network_permission(self):
        self.reject_xml('</manifest>', '<uses-permission android:name="android.permission.INTERNET"/></manifest>')

    def test_microphone_permission(self):
        self.reject_xml('android.permission.REORDER_TASKS', 'android.permission.RECORD_AUDIO')

    def test_duplicate_permission(self):
        self.reject_xml('</manifest>', '<uses-permission android:name="android.permission.REORDER_TASKS"/></manifest>')

    def test_custom_permission(self):
        self.reject_xml('</manifest>', '<permission android:name="other.permission"/></manifest>')

    def test_missing_manifest_entry(self):
        del self.entries['AndroidManifest.xml']
        with self.assertRaises(AssertionError): self.verify()

    def test_missing_dex(self):
        del self.entries['classes.dex']
        with self.assertRaises(AssertionError): self.verify()

    def test_v1_signature(self):
        self.entries['META-INF/CERT.RSA'] = b'signature'
        with self.assertRaises(AssertionError): self.verify()

    def test_other_abi(self):
        self.entries['lib/x86_64/libfoo.so'] = b'native'
        with self.assertRaises(AssertionError): self.verify()

    def test_duplicate_zip_entry(self):
        manifest = self.root / 'manifest.xml'
        apk = self.root / 'test.apk'
        manifest.write_text(self.xml, encoding='utf-8')
        with zipfile.ZipFile(apk, 'w') as archive:
            archive.writestr('AndroidManifest.xml', b'manifest')
            archive.writestr('classes.dex', b'dex')
            archive.writestr('classes.dex', b'dex2')
        with self.assertRaises(AssertionError): audit.verify(manifest, apk)

    def test_corrupt_crc(self):
        manifest = self.root / 'manifest.xml'
        apk = self.root / 'test.apk'
        manifest.write_text(self.xml, encoding='utf-8')
        with zipfile.ZipFile(apk, 'w', compression=zipfile.ZIP_STORED) as archive:
            archive.writestr('AndroidManifest.xml', b'manifest')
            archive.writestr('classes.dex', b'dex')
        data = apk.read_bytes().replace(b'dex', b'bad', 1)
        apk.write_bytes(data)
        with self.assertRaises(AssertionError): audit.verify(manifest, apk)

    def test_v2_v3_block(self):
        manifest = self.root / 'manifest.xml'
        apk = self.root / 'test.apk'
        manifest.write_text(self.xml, encoding='utf-8')
        with zipfile.ZipFile(apk, 'w') as archive:
            archive.writestr('AndroidManifest.xml', b'manifest')
            archive.writestr('classes.dex', b'dex')
        with zipfile.ZipFile(apk) as archive:
            central = archive.start_dir
        # A signed APK places this footer directly before the central directory.
        data = bytearray(apk.read_bytes())
        data[central - 16:central] = b'APK Sig Block 42'
        apk.write_bytes(data)
        with self.assertRaises(AssertionError): audit.verify(manifest, apk)


if __name__ == '__main__':
    unittest.main()
