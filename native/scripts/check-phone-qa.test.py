import hashlib,importlib.util,json,os,pathlib,struct,subprocess,sys,tempfile,unittest,zipfile
import shutil
from unittest.mock import patch

class LicenseCheckoutBytesTest(unittest.TestCase):
 def test_git_filters_preserve_notice_bytes_with_autocrlf(self):
  source=pathlib.Path(__file__).resolve().parents[2]
  with tempfile.TemporaryDirectory() as temp:
   root=pathlib.Path(temp)
   subprocess.run(['git','init','-q',str(root)],check=True)
   subprocess.run(['git','config','--local','core.autocrlf','true'],cwd=root,check=True)
   shutil.copyfile(source/'.gitattributes',root/'.gitattributes')
   samples={
    'native/licenses/react-native-template-MIT.txt':b'upstream\nlicense\n',
    'native/licenses/framework/react-native-LICENSE.txt':b'upstream\r\nlicense\r\n',
    'native/licenses/sign-to-speak/NOTICE':b'application\nnotice\n',
   }
   for path,original in samples.items():
    with self.subTest(path=path):
     saved=subprocess.run(['git','hash-object','-w','--path',path,'--stdin'],cwd=root,input=original,capture_output=True,check=True).stdout.strip().decode('ascii')
     clean=subprocess.run(['git','cat-file','blob',saved],cwd=root,capture_output=True,check=True).stdout
     checkout=subprocess.run(['git','cat-file','--filters','--path',path,saved],cwd=root,capture_output=True,check=True).stdout
     self.assertEqual(clean,original,'Git clean filter changed notice bytes')
     self.assertEqual(checkout,original,'Git checkout filter changed notice bytes')

spec=importlib.util.spec_from_file_location('qa',pathlib.Path(__file__).with_name('check-phone-qa.py'))
qa=importlib.util.module_from_spec(spec);spec.loader.exec_module(qa)
class PhoneQaApkTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=pathlib.Path(self.tmp.name)
  self.xml=f'''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{qa.PACKAGE}"><uses-sdk android:minSdkVersion="26" android:targetSdkVersion="36"/><uses-permission android:name="android.permission.CAMERA"/><uses-permission android:name="{qa.PACKAGE}.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"/><permission android:name="{qa.PACKAGE}.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION" android:protectionLevel="signature"/><application android:allowAudioPlaybackCapture="false" android:allowBackup="false" android:usesCleartextTraffic="false" android:testOnly="true"/></manifest>'''
  self.models={name:(name+'-fixture').encode() for name in qa.POCKET_MODEL}
  self.notice_root=self.root/'pocket-assets'/'pocket-licenses';self.notice_root.mkdir(parents=True)
  (self.notice_root/'cargo-declaration-routes.json').write_text(json.dumps({'status':'declarations-selected-for-component-probe','packages':[{}]*133,'blocked_packages':[]}))
  (self.notice_root/'NOTICE.txt').write_bytes(b'fixture notice')
  self.patch_model=patch.object(qa,'POCKET_MODEL',{name:(len(data),hashlib.sha256(data).hexdigest()) for name,data in self.models.items()});self.patch_model.start();self.addCleanup(self.patch_model.stop)
  self.patch_root=patch.object(qa,'POCKET_ROOT',self.root/'pocket-assets');self.patch_root.start();self.addCleanup(self.patch_root.stop)
  self.entries={'AndroidManifest.xml':b'fixture','classes.dex':b'fixture','lib/arm64-v8a/libappmodules.so':b'fixture','lib/arm64-v8a/libsign_pocket_tts_runtime.so':b'fixture','lib/arm64-v8a/libprivate_pocket_tts.so':b'fixture','lib/arm64-v8a/libc++_shared.so':b'fixture','assets/index.android.bundle':b'fixture','assets/react-native-template-MIT.txt':(qa.LICENSE_ROOT/'react-native-template-MIT.txt').read_bytes(),**{'assets/pocket-alba/'+name:data for name,data in self.models.items()},**{'assets/pocket-licenses/'+p.name:p.read_bytes() for p in self.notice_root.iterdir()},**{'assets/sign-to-speak/'+name:(qa.LICENSE_ROOT/'sign-to-speak'/name).read_bytes() for name in ('LICENSE','NOTICE')},**{'assets/framework/'+name:(qa.LICENSE_ROOT/'framework'/name).read_bytes() for name in ('react-native-LICENSE.txt','react-native-safe-area-context-LICENSE.txt','ndk-libcxx-NOTICE.txt','ndk-libcxxabi-NOTICE.txt')}}
 def verify(self):
  m=self.root/'manifest.xml';m.write_text(self.xml);a=self.root/'app.apk'
  with zipfile.ZipFile(a,'w') as z:
   for name,data in self.entries.items():z.writestr(name,data)
  qa.verify(m,a)
 def test_valid_unsigned_arm64_qa(self):self.verify()
 def test_tensorflow_lite_library_is_not_retired_flite(self):
  self.entries['lib/arm64-v8a/libtensorflowlite_jni.so']=b'fixture'
  self.entries['assets/models/hand_landmarks.tflite']=b'TFL3'
  self.verify()
 def test_retired_flite_library_rejected(self):
  self.entries['lib/arm64-v8a/libflite_usenglish.so']=b'old'
  with self.assertRaisesRegex(AssertionError,'Retired speech runtime'):self.verify()
 def test_missing_synthesis_library_rejected(self):
  del self.entries['lib/arm64-v8a/libprivate_pocket_tts.so']
  with self.assertRaisesRegex(AssertionError,'Pocket JNI bridge'):self.verify()
 def test_missing_javascript_bundle_rejected(self):
  del self.entries['assets/index.android.bundle']
  with self.assertRaisesRegex(AssertionError,'bundled JavaScript'):self.verify()
 def test_empty_javascript_bundle_rejected(self):
  self.entries['assets/index.android.bundle']=b''
  with self.assertRaisesRegex(AssertionError,'bundled JavaScript'):self.verify()
 def test_missing_pocket_model_rejected(self):
  del self.entries['assets/pocket-alba/model.safetensors']
  with self.assertRaisesRegex(AssertionError,'pinned Alba set'):self.verify()
 def test_extra_pocket_model_rejected(self):
  self.entries['assets/pocket-alba/extra.bin']=b'extra'
  with self.assertRaisesRegex(AssertionError,'pinned Alba set'):self.verify()
 def test_corrupt_pocket_model_rejected(self):
  self.entries['assets/pocket-alba/model.safetensors']=b'x'*len(self.models['model.safetensors'])
  with self.assertRaisesRegex(AssertionError,'SHA-256 mismatch'):self.verify()
 def test_changed_pocket_notice_rejected(self):
  self.entries['assets/pocket-licenses/NOTICE.txt']=b'changed'
  with self.assertRaisesRegex(AssertionError,'Pocket notice changed'):self.verify()
 def test_retired_flite_asset_rejected(self):
  self.entries['assets/flite/THIRD_PARTY_NOTICES.txt']=b'old'
  with self.assertRaisesRegex(AssertionError,'Retired Flite'):self.verify()
 def test_missing_application_license_rejected(self):
  del self.entries['assets/sign-to-speak/LICENSE']
  with self.assertRaisesRegex(AssertionError,'application license asset'):self.verify()
 def test_changed_application_license_rejected(self):
  self.entries['assets/sign-to-speak/LICENSE']=b'changed'
  with self.assertRaisesRegex(AssertionError,'application license asset'):self.verify()
 def test_missing_application_notice_rejected(self):
  del self.entries['assets/sign-to-speak/NOTICE']
  with self.assertRaisesRegex(AssertionError,'application license asset'):self.verify()
 def test_changed_application_notice_rejected(self):
  self.entries['assets/sign-to-speak/NOTICE']=b'changed'
  with self.assertRaisesRegex(AssertionError,'application license asset'):self.verify()
 def test_missing_framework_notice_rejected(self):
  for name in ('react-native-LICENSE.txt','react-native-safe-area-context-LICENSE.txt','ndk-libcxx-NOTICE.txt','ndk-libcxxabi-NOTICE.txt'):
   with self.subTest(name=name):
    entry='assets/framework/'+name; data=self.entries.pop(entry)
    try:
     with self.assertRaisesRegex(AssertionError,'framework license asset'):self.verify()
    finally:self.entries[entry]=data
 def test_changed_framework_notice_rejected(self):
  for name in ('react-native-LICENSE.txt','react-native-safe-area-context-LICENSE.txt','ndk-libcxx-NOTICE.txt','ndk-libcxxabi-NOTICE.txt'):
   with self.subTest(name=name):
    entry='assets/framework/'+name; data=self.entries[entry];self.entries[entry]=b'changed'
    try:
     with self.assertRaisesRegex(AssertionError,'framework license asset'):self.verify()
    finally:self.entries[entry]=data
 def test_extra_framework_notice_rejected(self):
  self.entries['assets/framework/unreviewed.txt']=b'extra'
  with self.assertRaisesRegex(AssertionError,'framework license asset set'):self.verify()
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
 def test_duplicate_sdk_rejected(self):
  self.xml=self.xml.replace('</manifest>','<uses-sdk android:minSdkVersion="26" android:targetSdkVersion="36"/></manifest>')
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
 def test_duplicate_zip_name_rejected(self):
  manifest=self.root/'manifest.xml';manifest.write_text(self.xml,encoding='utf-8')
  apk=self.root/'app.apk'
  with zipfile.ZipFile(apk,'w') as archive:
   for name,data in self.entries.items():archive.writestr(name,data)
   archive.writestr('classes.dex',b'shadow')
  with self.assertRaisesRegex(AssertionError,'Duplicate APK ZIP entry'):qa.verify(manifest,apk)
 def test_optimized_cli_rejects_wrong_identity(self):
  self.xml=self.xml.replace(qa.PACKAGE,qa.PACKAGE.removesuffix('.qa'))
  manifest=self.root/'manifest.xml';manifest.write_text(self.xml,encoding='utf-8')
  apk=self.root/'app.apk'
  with zipfile.ZipFile(apk,'w') as archive:
   for name,data in self.entries.items():archive.writestr(name,data)
  for args,environment in ((['-O'],os.environ.copy()),([],dict(os.environ,PYTHONOPTIMIZE='1'))):
   with self.subTest(mode=args or 'PYTHONOPTIMIZE'):
    result=subprocess.run([sys.executable,*args,str(pathlib.Path(qa.__file__)),str(manifest),str(apk)],capture_output=True,text=True,env=environment)
    self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
    self.assertIn('APK must use the isolated QA identity',result.stderr)
if __name__=='__main__':unittest.main()
