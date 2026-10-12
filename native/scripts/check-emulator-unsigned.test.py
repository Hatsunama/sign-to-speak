import importlib.util,pathlib,tempfile,unittest,zipfile
p=pathlib.Path(__file__).with_name('check-emulator-unsigned.py');s=importlib.util.spec_from_file_location('audit',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Unsigned(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=pathlib.Path(self.tmp.name)
  self.app=f'''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{m.QA}"><uses-sdk android:minSdkVersion="26" android:targetSdkVersion="36"/><uses-permission android:name="android.permission.CAMERA"/><uses-permission android:name="{m.QA}.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"/><permission android:name="{m.QA}.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION" android:protectionLevel="signature"/><application android:testOnly="true" android:debuggable="false" android:allowAudioPlaybackCapture="false" android:allowBackup="false" android:usesCleartextTraffic="false"/></manifest>'''
  self.test=f'''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{m.QA}.test"><uses-sdk android:minSdkVersion="26" android:targetSdkVersion="36"/><uses-permission android:name="android.permission.REORDER_TASKS"/><application android:testOnly="true"/><instrumentation android:name="androidx.test.runner.AndroidJUnitRunner" android:targetPackage="{m.QA}"/></manifest>'''
  self.files={'AndroidManifest.xml':b'x','classes.dex':b'x','lib/x86_64/libappmodules.so':b'x','assets/index.android.bundle':b'x'}
 def verify(self):
  for role,xml in [('app',self.app),('test',self.test)]:
   (self.root/(role+'.xml')).write_text(xml)
   with zipfile.ZipFile(self.root/(role+'.apk'),'w') as z:
    for name,data in self.files.items():z.writestr(name,data)
  m.verify(self.root/'app.xml',self.root/'app.apk',self.root/'test.xml',self.root/'test.apk')
 def test_valid(self):self.verify()
 def test_measured_instrumentation_manifest(self):
  self.test=pathlib.Path(__file__).with_name('fixtures').joinpath('emulator-test-manifest.xml').read_text()
  self.verify()
 def test_reject_identity(self):
  self.app=self.app.replace(m.QA,m.QA.removesuffix('.qa'))
  with self.assertRaises(AssertionError):self.verify()
 def test_reject_arm(self):
  self.files['lib/arm64-v8a/libappmodules.so']=b'x'
  with self.assertRaises(AssertionError):self.verify()
 def test_reject_signed(self):
  self.files['META-INF/CERT.RSA']=b'x'
  with self.assertRaises(AssertionError):self.verify()
 def test_reject_no_bundle(self):
  del self.files['assets/index.android.bundle']
  with self.assertRaises(AssertionError):self.verify()
 def test_reject_debuggable(self):
  self.app=self.app.replace('debuggable="false"','debuggable="true"')
  with self.assertRaises(AssertionError):self.verify()
 def test_reject_nontest_app(self):
  self.app=self.app.replace('testOnly="true"','testOnly="false"')
  with self.assertRaises(AssertionError):self.verify()
 def test_reject_nontest_instrumentation(self):
  self.test=self.test.replace('testOnly="true"','testOnly="false"')
  with self.assertRaises(AssertionError):self.verify()
 def test_reject_wrong_target(self):
  self.test=self.test.replace('targetPackage="'+m.QA+'"','targetPackage="other.app"')
  with self.assertRaises(AssertionError):self.verify()
 def test_reject_test_network(self):
  self.test=self.test.replace('</manifest>','<uses-permission android:name="android.permission.INTERNET"/></manifest>')
  with self.assertRaises(AssertionError):self.verify()
 def test_reject_duplicate_test_permission(self):
  self.test=self.test.replace('</manifest>','<uses-permission android:name="android.permission.REORDER_TASKS"/></manifest>')
  with self.assertRaises(AssertionError):self.verify()
 def test_reject_extra_test_camera(self):
  self.test=self.test.replace('</manifest>','<uses-permission android:name="android.permission.CAMERA"/></manifest>')
  with self.assertRaises(AssertionError):self.verify()
 def test_reject_test_permission_declaration(self):
  self.test=self.test.replace('</manifest>','<permission android:name="other.permission"/></manifest>')
  with self.assertRaises(AssertionError):self.verify()
 def test_reject_wrong_test_sdk(self):
  self.test=self.test.replace('targetSdkVersion="36"','targetSdkVersion="35"')
  with self.assertRaises(AssertionError):self.verify()
 def test_reject_missing_playback_capture_policy(self):
  self.app=self.app.replace(' android:allowAudioPlaybackCapture="false"','')
  with self.assertRaises(AssertionError):self.verify()
 def test_reject_playback_capture_allowed(self):
  self.app=self.app.replace('allowAudioPlaybackCapture="false"','allowAudioPlaybackCapture="true"')
  with self.assertRaises(AssertionError):self.verify()
 def test_reject_wrong_playback_capture_namespace(self):
  self.app=self.app.replace('android:allowAudioPlaybackCapture','allowAudioPlaybackCapture')
  with self.assertRaises(AssertionError):self.verify()
 def test_reject_duplicate_applications(self):
  self.app=self.app.replace('</manifest>','<application android:allowAudioPlaybackCapture="true"/></manifest>')
  with self.assertRaises(AssertionError):self.verify()
if __name__=='__main__':unittest.main()
