import importlib.util,pathlib,struct,tempfile,unittest,zipfile
spec=importlib.util.spec_from_file_location('qa',pathlib.Path(__file__).with_name('check-phone-qa.py'))
qa=importlib.util.module_from_spec(spec);spec.loader.exec_module(qa)
class PhoneQaApkTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=pathlib.Path(self.tmp.name)
  self.xml=f'''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{qa.PACKAGE}"><uses-sdk android:minSdkVersion="26" android:targetSdkVersion="36"/><uses-permission android:name="android.permission.CAMERA"/><uses-permission android:name="{qa.PACKAGE}.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"/><permission android:name="{qa.PACKAGE}.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION" android:protectionLevel="signature"/><application android:allowAudioPlaybackCapture="false" android:allowBackup="false" android:usesCleartextTraffic="false" android:testOnly="true"/></manifest>'''
  self.entries={'AndroidManifest.xml':b'fixture','classes.dex':b'fixture','lib/arm64-v8a/libappmodules.so':b'fixture','assets/index.android.bundle':b'fixture'}
 def verify(self):
  m=self.root/'manifest.xml';m.write_text(self.xml);a=self.root/'app.apk'
  with zipfile.ZipFile(a,'w') as z:
   for name,data in self.entries.items():z.writestr(name,data)
  qa.verify(m,a)
 def test_valid_unsigned_arm64_qa(self):self.verify()
 def test_missing_javascript_bundle_rejected(self):
  del self.entries['assets/index.android.bundle']
  with self.assertRaisesRegex(AssertionError,'bundled JavaScript'):self.verify()
 def test_empty_javascript_bundle_rejected(self):
  self.entries['assets/index.android.bundle']=b''
  with self.assertRaisesRegex(AssertionError,'bundled JavaScript'):self.verify()
 def test_shipping_id_rejected(self):
  self.xml=self.xml.replace(qa.PACKAGE,qa.PACKAGE.removesuffix('.qa'))
  with self.assertRaises(AssertionError):self.verify()
 def test_x86_rejected(self):
  self.entries['lib/x86_64/libappmodules.so']=b'fixture'
  with self.assertRaises(AssertionError):self.verify()
 def test_signature_rejected(self):
  self.entries['META-INF/CERT.RSA']=b'fixture'
  with self.assertRaises(AssertionError):self.verify()
 def test_apk_signing_block_rejected(self):
  self.verify()
  apk=self.root/'app.apk';data=apk.read_bytes()
  with zipfile.ZipFile(apk) as z:central=z.start_dir
  block=struct.pack('<Q',24)+struct.pack('<Q',24)+b'APK Sig Block 42'
  data=bytearray(data[:central]+block+data[central:])
  eocd=data.rfind(b'PK\x05\x06')
  struct.pack_into('<I',data,eocd+16,central+len(block));apk.write_bytes(data)
  with self.assertRaisesRegex(AssertionError,'Unexpected APK signing block'):
   qa.verify(self.root/'manifest.xml',apk)
 def test_not_test_only_rejected(self):
  self.xml=self.xml.replace('testOnly="true"','testOnly="false"')
  with self.assertRaises(AssertionError):self.verify()
 def test_network_rejected(self):
  self.xml=self.xml.replace('</manifest>','<uses-permission-sdk-23 android:name="android.permission.INTERNET"/></manifest>')
  with self.assertRaises(AssertionError):self.verify()
 def test_permission_weakening_rejected(self):
  self.xml=self.xml.replace('protectionLevel="signature"','protectionLevel="normal"')
  with self.assertRaises(AssertionError):self.verify()
 def test_wrong_sdk_rejected(self):
  self.xml=self.xml.replace('targetSdkVersion="36"','targetSdkVersion="35"')
  with self.assertRaises(AssertionError):self.verify()
 def test_missing_playback_capture_policy_rejected(self):
  self.xml=self.xml.replace(' android:allowAudioPlaybackCapture="false"','')
  with self.assertRaises(AssertionError):self.verify()
 def test_playback_capture_allowed_rejected(self):
  self.xml=self.xml.replace('allowAudioPlaybackCapture="false"','allowAudioPlaybackCapture="true"')
  with self.assertRaises(AssertionError):self.verify()
 def test_wrong_playback_capture_namespace_rejected(self):
  self.xml=self.xml.replace('android:allowAudioPlaybackCapture','allowAudioPlaybackCapture')
  with self.assertRaises(AssertionError):self.verify()
 def test_duplicate_applications_rejected(self):
  self.xml=self.xml.replace('</manifest>','<application android:allowAudioPlaybackCapture="true"/></manifest>')
  with self.assertRaises(AssertionError):self.verify()
if __name__=='__main__':unittest.main()
