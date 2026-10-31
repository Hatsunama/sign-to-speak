import hashlib,importlib.util,json,os,pathlib,subprocess,sys,tempfile,unittest,zipfile
from unittest.mock import patch
p=pathlib.Path(__file__).with_name('check-emulator-unsigned.py');s=importlib.util.spec_from_file_location('audit',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Unsigned(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=pathlib.Path(self.tmp.name)
  self.app=f'''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{m.QA}"><uses-sdk android:minSdkVersion="26" android:targetSdkVersion="36"/><uses-permission android:name="android.permission.CAMERA"/><uses-permission android:name="{m.QA}.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"/><permission android:name="{m.QA}.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION" android:protectionLevel="signature"/><application android:testOnly="true" android:debuggable="false" android:allowAudioPlaybackCapture="false" android:allowBackup="false" android:usesCleartextTraffic="false"/></manifest>'''
  self.test=f'''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{m.QA}.test"><uses-sdk android:minSdkVersion="26" android:targetSdkVersion="36"/><uses-permission android:name="android.permission.REORDER_TASKS"/><application android:testOnly="true"/><instrumentation android:name="androidx.test.runner.AndroidJUnitRunner" android:targetPackage="{m.QA}"/></manifest>'''
  self.models={name:(name+'-fixture').encode() for name in m.phone.POCKET_MODEL}
  self.notice_root=self.root/'pocket-assets'/'pocket-licenses';self.notice_root.mkdir(parents=True)
  (self.notice_root/'cargo-declaration-routes.json').write_text(json.dumps({'status':'declarations-selected-for-component-probe','packages':[{}]*133,'blocked_packages':[]}))
  (self.notice_root/'NOTICE.txt').write_bytes(b'fixture notice')
  self.patch_model=patch.object(m.phone,'POCKET_MODEL',{name:(len(data),hashlib.sha256(data).hexdigest()) for name,data in self.models.items()});self.patch_model.start();self.addCleanup(self.patch_model.stop)
  self.patch_root=patch.object(m.phone,'POCKET_ROOT',self.root/'pocket-assets');self.patch_root.start();self.addCleanup(self.patch_root.stop)
  self.files={'AndroidManifest.xml':b'x','classes.dex':b'x','lib/x86_64/libappmodules.so':b'x','lib/x86_64/libsign_pocket_tts_runtime.so':b'x','lib/x86_64/libprivate_pocket_tts.so':b'x','lib/x86_64/libc++_shared.so':b'x','assets/index.android.bundle':b'x',**{'assets/pocket-alba/'+name:data for name,data in self.models.items()},**{'assets/pocket-licenses/'+p.name:p.read_bytes() for p in self.notice_root.iterdir()},**{'assets/sign-to-speak/'+name:(m.phone.LICENSE_ROOT/'sign-to-speak'/name).read_bytes() for name in m.phone.REQUIRED_APPLICATION_ASSETS},'assets/react-native-template-MIT.txt':(m.phone.LICENSE_ROOT/'react-native-template-MIT.txt').read_bytes(),**{'assets/framework/'+name:(m.phone.LICENSE_ROOT/'framework'/name).read_bytes() for name in m.phone.REQUIRED_FRAMEWORK_ASSETS}}
  receipt=json.loads((m.phone.LICENSE_ROOT/'bundled-js'/'reviewed-manifest.json').read_text())
  self.files['assets/bundled-js/reviewed-manifest.json']=(m.phone.LICENSE_ROOT/'bundled-js'/'reviewed-manifest.json').read_bytes()
  for asset in {row['asset'] for row in receipt['packages'] if row['asset'].startswith('bundled-js/')}:
   self.files['assets/'+asset]=(m.phone.LICENSE_ROOT/asset).read_bytes()
 def verify(self):
  for role,xml in [('app',self.app),('test',self.test)]:
   (self.root/(role+'.xml')).write_text(xml)
   with zipfile.ZipFile(self.root/(role+'.apk'),'w') as z:
    for name,data in self.files.items():z.writestr(name,data)
  m.verify(self.root/'app.xml',self.root/'app.apk',self.root/'test.xml',self.root/'test.apk')
 def test_valid(self):self.verify()
 def test_tensorflow_lite_library_is_not_retired_flite(self):
  self.files['lib/x86_64/libtensorflowlite_jni.so']=b'fixture'
  self.files['assets/models/hand_landmarks.tflite']=b'TFL3'
  self.verify()
 def test_retired_flite_library_rejected(self):
  self.files['lib/x86_64/libflite_usenglish.so']=b'old'
  with self.assertRaises(AssertionError):self.verify()
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
 def test_reject_missing_model(self):
  del self.files['assets/pocket-alba/tokenizer.model']
  with self.assertRaisesRegex(AssertionError,'pinned Alba set'):self.verify()
 def test_reject_corrupt_model(self):
  self.files['assets/pocket-alba/tokenizer.model']=b'x'*len(self.models['tokenizer.model'])
  with self.assertRaisesRegex(AssertionError,'SHA-256 mismatch'):self.verify()
 def test_reject_extra_model(self):
  self.files['assets/pocket-alba/extra.bin']=b'x'
  with self.assertRaisesRegex(AssertionError,'pinned Alba set'):self.verify()
 def test_reject_empty_app_bundle(self):
  self.files['assets/index.android.bundle']=b''
  with self.assertRaisesRegex(AssertionError,'nonempty bundled JavaScript'):self.verify()
 def test_reject_missing_framework_notice(self):
  del self.files['assets/framework/react-native-LICENSE.txt']
  with self.assertRaisesRegex(AssertionError,'framework license asset'):self.verify()
 def test_reject_changed_framework_notice(self):
  self.files['assets/framework/ndk-libcxx-NOTICE.txt']=b'changed'
  with self.assertRaisesRegex(AssertionError,'framework license asset'):self.verify()
 def test_instrumentation_without_bundle(self):
  apk=self.root/'instrumentation.apk'
  with zipfile.ZipFile(apk,'w') as z:
   z.writestr('AndroidManifest.xml',b'x')
   z.writestr('classes.dex',b'x')
  m.unsigned(apk)
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
 def test_reject_duplicate_app_sdk(self):
  self.app=self.app.replace('</manifest>','<uses-sdk android:minSdkVersion="26" android:targetSdkVersion="36"/></manifest>')
  with self.assertRaises(AssertionError):self.verify()
 def test_reject_duplicate_test_sdk(self):
  self.test=self.test.replace('</manifest>','<uses-sdk android:minSdkVersion="26" android:targetSdkVersion="36"/></manifest>')
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
 def test_reject_unplanned_instrumentation_components(self):
  for tag in ('service','receiver','provider'):
   with self.subTest(tag=tag):
    original=self.test
    self.test=self.test.replace('<application android:testOnly="true"/>',f'<application android:testOnly="true"><{tag} android:name="Other"/></application>')
    try:
     with self.assertRaises(AssertionError):self.verify()
    finally:self.test=original
 def test_reject_debuggable_instrumentation(self):
  self.test=self.test.replace('android:testOnly="true"','android:testOnly="true" android:debuggable="true"')
  with self.assertRaises(AssertionError):self.verify()
 def test_reject_duplicate_zip_names(self):
  self.verify()
  for role in ('app','test'):
   with self.subTest(role=role):
    apk=self.root/(role+'.apk')
    with zipfile.ZipFile(apk,'a') as archive:archive.writestr('classes.dex',b'shadow')
    with self.assertRaisesRegex(AssertionError,'Duplicate APK ZIP entry'):
     m.verify(self.root/'app.xml',self.root/'app.apk',self.root/'test.xml',self.root/'test.apk')
    self.verify()
 def test_optimized_cli_rejects_wrong_identity(self):
  self.verify()
  app=self.root/'app.xml';app.write_text(self.app.replace(m.QA,m.QA.removesuffix('.qa')),encoding='utf-8')
  for args,environment in ((['-O'],os.environ.copy()),([],dict(os.environ,PYTHONOPTIMIZE='1'))):
   with self.subTest(mode=args or 'PYTHONOPTIMIZE'):
    result=subprocess.run([sys.executable,*args,str(p),str(app),str(self.root/'app.apk'),str(self.root/'test.xml'),str(self.root/'test.apk')],capture_output=True,text=True,env=environment)
    self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
    self.assertIn('AssertionError: APK validation failed',result.stderr)
if __name__=='__main__':unittest.main()
