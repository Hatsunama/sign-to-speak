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
    'native/licenses/bundled-js/abort-controller-LICENSE.txt':b'upstream\r\nlicense\r\n',
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
 def test_resolved_maven_grants_are_required_and_pinned(self):
  expected={'bolts-tasks-LICENSE.txt':'9e69750c616877af2dec86f961a53e39324c3655e25b1d6ceed06e9661ec072b',
            'old-facebook-MIT.txt':'52412d7bc7ce4157ea628bbaacb8829e0a9cb3c58f57f99176126bc8cf2bfc85',
            'camera-libyuv-LICENSE.txt':'2b2cc1180c7e6988328ad2033b04b80117419db9c4c584918bbb3cfec7e9364f'}
  self.assertTrue(expected.keys() <= set(qa.REQUIRED_FRAMEWORK_ASSETS))
  for name,digest in expected.items():
   with self.subTest(name=name):
    self.assertEqual(qa.FRAMEWORK_SHA256[name],digest)
    self.assertEqual(hashlib.sha256((qa.LICENSE_ROOT/'framework'/name).read_bytes()).hexdigest(),digest)
 def test_hermes_and_ijg_notices_are_required_and_pinned(self):
  expected={'hermes-dtoa.c-NOTICE.txt','hermes-g_fmt.c-NOTICE.txt','fresco-ijg-README.txt','fresco-ijg-ACKNOWLEDGMENT.txt'}
  self.assertTrue(expected.issubset(qa.REQUIRED_FRAMEWORK_ASSETS))
  for name in expected:
   with self.subTest(name=name):
    self.assertEqual(qa.FRAMEWORK_SHA256[name],hashlib.sha256((qa.LICENSE_ROOT/'framework'/name).read_bytes()).hexdigest())
  self.assertIn(b'This software is based in part on the work of the Independent JPEG Group.',
                (qa.LICENSE_ROOT/'framework'/'fresco-ijg-ACKNOWLEDGMENT.txt').read_bytes())
 def test_native_dependency_notices_are_required_and_pinned(self):
  expected={'double-conversion-LICENSE.txt','glog-COPYING.txt','fast_float-LICENSE-MIT.txt','folly-LICENSE.txt'}
  self.assertTrue(expected.issubset(qa.REQUIRED_FRAMEWORK_ASSETS))
  for name in expected:
   with self.subTest(name=name):
    self.assertEqual(qa.FRAMEWORK_SHA256[name],hashlib.sha256((qa.LICENSE_ROOT/'framework'/name).read_bytes()).hexdigest())
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=pathlib.Path(self.tmp.name)
  self.xml=f'''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{qa.PACKAGE}"><uses-sdk android:minSdkVersion="26" android:targetSdkVersion="36"/><uses-permission android:name="android.permission.CAMERA"/><uses-permission android:name="{qa.PACKAGE}.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"/><permission android:name="{qa.PACKAGE}.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION" android:protectionLevel="signature"/><application android:allowAudioPlaybackCapture="false" android:allowBackup="false" android:usesCleartextTraffic="false" android:testOnly="true"/></manifest>'''
  self.models={name:(name+'-fixture').encode() for name in qa.POCKET_MODEL}
  self.notice_root=self.root/'pocket-assets'/'pocket-licenses';self.notice_root.mkdir(parents=True)
  (self.notice_root/'cargo-declaration-routes.json').write_text(json.dumps({'status':'declarations-selected-for-component-probe','packages':[{}]*133,'blocked_packages':[]}))
  (self.notice_root/'NOTICE.txt').write_bytes(b'fixture notice')
  self.patch_model=patch.object(qa,'POCKET_MODEL',{name:(len(data),hashlib.sha256(data).hexdigest()) for name,data in self.models.items()});self.patch_model.start();self.addCleanup(self.patch_model.stop)
  self.patch_root=patch.object(qa,'POCKET_ROOT',self.root/'pocket-assets');self.patch_root.start();self.addCleanup(self.patch_root.stop)
  self.entries={'AndroidManifest.xml':b'fixture','classes.dex':b'fixture','lib/arm64-v8a/libappmodules.so':b'fixture','lib/arm64-v8a/libsign_pocket_tts_runtime.so':b'fixture','lib/arm64-v8a/libprivate_pocket_tts.so':b'fixture','lib/arm64-v8a/libc++_shared.so':b'fixture','assets/index.android.bundle':b'fixture','assets/react-native-template-MIT.txt':(qa.LICENSE_ROOT/'react-native-template-MIT.txt').read_bytes(),**{'assets/pocket-alba/'+name:data for name,data in self.models.items()},**{'assets/pocket-licenses/'+p.name:p.read_bytes() for p in self.notice_root.iterdir()},**{'assets/sign-to-speak/'+name:(qa.LICENSE_ROOT/'sign-to-speak'/name).read_bytes() for name in ('LICENSE','NOTICE')},**{'assets/framework/'+name:(qa.LICENSE_ROOT/'framework'/name).read_bytes() for name in qa.REQUIRED_FRAMEWORK_ASSETS}}
  receipt=json.loads((qa.LICENSE_ROOT/'bundled-js'/'reviewed-manifest.json').read_text())
  self.entries['assets/bundled-js/reviewed-manifest.json']=(qa.LICENSE_ROOT/'bundled-js'/'reviewed-manifest.json').read_bytes()
  for asset in {row['asset'] for row in receipt['packages'] if row['asset'].startswith('bundled-js/')}:
   self.entries['assets/'+asset]=(qa.LICENSE_ROOT/asset).read_bytes()
 def verify(self):
  m=self.root/'manifest.xml';m.write_text(self.xml);a=self.root/'app.apk'
  with zipfile.ZipFile(a,'w') as z:
   for name,data in self.entries.items():z.writestr(name,data)
  qa.verify(m,a)
 def test_valid_unsigned_arm64_qa(self):self.verify()
 def test_frozen_actual_bundle_inventory(self):
  receipt=json.loads((qa.LICENSE_ROOT/'bundled-js'/'reviewed-manifest.json').read_text())
  expected={'@babel/runtime@7.29.10','@react-native/assets-registry@0.86.3','@react-native/js-polyfills@0.86.3','@react-native/normalize-colors@0.86.3','@react-native/virtualized-lists@0.86.3','abort-controller@3.0.0','base64-js@1.5.1','event-target-shim@5.0.1','invariant@2.2.4','memoize-one@5.2.1','metro-runtime@0.84.6','nullthrows@1.1.1','promise@8.3.0','react@19.2.3','react-native@0.86.3','react-native-safe-area-context@5.7.0','regenerator-runtime@0.13.11','scheduler@0.27.0','stacktrace-parser@0.1.11','whatwg-fetch@3.6.20'}
  self.assertEqual({p['name']+'@'+p['version'] for p in receipt['packages']},expected)
  self.assertEqual({p['license'] for p in receipt['packages']},{'MIT'})
 def test_missing_bundle_notice_rejected(self):
  self.entries.pop('assets/bundled-js/abort-controller-LICENSE.txt')
  with self.assertRaisesRegex(AssertionError,'bundled JS'):self.verify()
 def test_changed_bundle_notice_rejected(self):
  self.entries['assets/bundled-js/abort-controller-LICENSE.txt']=b'changed'
  with self.assertRaisesRegex(AssertionError,'bundled JS'):self.verify()
 def test_extra_bundle_notice_rejected(self):
  self.entries['assets/bundled-js/unreviewed.txt']=b'extra'
  with self.assertRaisesRegex(AssertionError,'bundled JS'):self.verify()
 def test_missing_bundle_receipt_rejected(self):
  self.entries.pop('assets/bundled-js/reviewed-manifest.json')
  with self.assertRaisesRegex(AssertionError,'bundled JS'):self.verify()
 def test_actual_inventory_rejects_package_mutations(self):
  receipt=json.loads((qa.LICENSE_ROOT/'bundled-js'/'reviewed-manifest.json').read_text())
  actual={'status':'COMPLETE','package_count':20,'unresolved_count':0,'packages':[{'name':p['name'],'version':p['version'],'license':p['license'],'notice_status':'resolved','license_files':[{'sha256':p['sha256']}]} for p in receipt['packages']]}
  qa.verify_bundled_js_inventory(actual)
  mutations=[]
  import copy
  for kind in ('missing','extra','wrong-version','wrong-grant','unresolved'):
   changed=copy.deepcopy(actual)
   if kind=='missing':changed['packages'].pop();changed['package_count']=19
   if kind=='extra':changed['packages'].append({'name':'unreviewed','version':'1.0.0','license':'MIT','notice_status':'resolved','license_files':[{'sha256':'0'*64}]});changed['package_count']=21
   if kind=='wrong-version':changed['packages'][0]['version']='999.0.0'
   if kind=='wrong-grant':changed['packages'][0]['license_files'][0]['sha256']=changed['packages'][1]['license_files'][0]['sha256']
   if kind=='unresolved':changed['status']='INCOMPLETE';changed['unresolved_count']=1
   mutations.append((kind,changed))
  for kind,changed in mutations:
   with self.subTest(kind=kind),self.assertRaisesRegex(AssertionError,'bundled JS'):qa.verify_bundled_js_inventory(changed)
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
  for name in qa.REQUIRED_FRAMEWORK_ASSETS:
   with self.subTest(name=name):
    entry='assets/framework/'+name; data=self.entries.pop(entry)
    try:
     with self.assertRaisesRegex(AssertionError,'framework license asset'):self.verify()
    finally:self.entries[entry]=data
 def test_changed_framework_notice_rejected(self):
  for name in qa.REQUIRED_FRAMEWORK_ASSETS:
   with self.subTest(name=name):
    entry='assets/framework/'+name; data=self.entries[entry];self.entries[entry]=b'changed'
    try:
     with self.assertRaisesRegex(AssertionError,'framework license asset'):self.verify()
    finally:self.entries[entry]=data
 def test_changed_native_dependency_source_rejected(self):
  copied=self.root/'copied-licenses'
  shutil.copytree(qa.LICENSE_ROOT,copied)
  with patch.object(qa,'LICENSE_ROOT',copied):
   for name in ('double-conversion-LICENSE.txt','glog-COPYING.txt','fast_float-LICENSE-MIT.txt','folly-LICENSE.txt'):
    with self.subTest(name=name):
     source=copied/'framework'/name; original=source.read_bytes();source.write_bytes(original+b'changed')
     try:
      with self.assertRaisesRegex(AssertionError,'source differs from reviewed bytes'):self.verify()
     finally:source.write_bytes(original)
 def test_changed_hermes_or_ijg_source_rejected(self):
  copied=self.root/'copied-licenses'
  shutil.copytree(qa.LICENSE_ROOT,copied)
  with patch.object(qa,'LICENSE_ROOT',copied):
   for name in ('hermes-dtoa.c-NOTICE.txt','hermes-g_fmt.c-NOTICE.txt','fresco-ijg-README.txt','fresco-ijg-ACKNOWLEDGMENT.txt'):
    with self.subTest(name=name):
     source=copied/'framework'/name; original=source.read_bytes();source.write_bytes(original+b'changed')
     try:
      with self.assertRaisesRegex(AssertionError,'source differs from reviewed bytes'):self.verify()
     finally:source.write_bytes(original)
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
