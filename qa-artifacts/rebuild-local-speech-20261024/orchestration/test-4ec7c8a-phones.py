import pathlib,subprocess,json,time,hashlib,sys
ROOT=pathlib.Path.cwd();OUT=ROOT/'java-check/rebuild-4ec7c8a-phone';ADB=r'C:\Users\1thea\AppData\Local\Android\Sdk\platform-tools\adb.exe';PKG='com.xmilo_at_your_side.sign_to_speak.qa';COMP=PKG+'/com.xmilo_at_your_side.sign_to_speak.MainActivity';RUNNER=PKG+'.test/androidx.test.runner.AndroidJUnitRunner'
PHONES={'s24':'R3CX90HWPGB','seeker':'SM02G4061962301'};results={'source':'4ec7c8a38d9444db4d6ef2b3506faa27b0b5a292','phones':{}};original={}
def run(serial,*args):
 p=subprocess.run([ADB,'-s',serial,*args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=45);s=p.stdout.decode('utf8','replace')
 if p.returncode:raise RuntimeError(s)
 return s
signing=json.loads((OUT/'signing-receipt.json').read_text());expected={a['role']:a['signed_sha256'] for a in signing['artifacts']}
try:
 for label,serial in PHONES.items():
  results['phones'][label]={'logStart':run(serial,'shell',"date '+%m-%d %H:%M:%S.000'").strip()}
  original[serial]={k:run(serial,'shell','settings','get','system',k).strip() for k in ('accelerometer_rotation','user_rotation')}
  run(serial,'shell','settings','put','system','accelerometer_rotation','0');run(serial,'shell','settings','put','system','user_rotation','0')
  for role in ('app','test'):
   assert 'Success' in run(serial,'install','-r','-t',str(OUT/(role+'-signed.apk')))
  installed={}
  for role,pkg in [('app',PKG),('test',PKG+'.test')]:
   path=run(serial,'shell','pm','path',pkg).strip().removeprefix('package:');assert path.startswith('/data/app/') and path.endswith('/base.apk')
   installed[role]=run(serial,'shell','sha256sum',path).split()[0];assert installed[role]==expected[role]
  run(serial,'shell','am','force-stop',PKG)
  cold=run(serial,'shell','am','start','-W','-n',COMP);assert 'Status: ok' in cold and 'LaunchState: COLD' in cold
  results['phones'][label].update({'installed_signed_sha256':installed,'cold_start':cold,'original_rotation':original[serial]})
 processes=[]
 for label,serial in PHONES.items():
  handle=(OUT/(label+'-portrait-instrumentation.txt')).open('wb');p=subprocess.Popen([ADB,'-s',serial,'shell','am','instrument','-w','-r','-e','class','com.xmilo_at_your_side.sign_to_speak.PhoneSpeechIntegrationTest,com.xmilo_at_your_side.sign_to_speak.PhonePreviewIntegrationTest',RUNNER],stdout=handle,stderr=subprocess.STDOUT);processes.append((label,serial,p,handle))
 print('Both phones: exact app/test hashes installed, COLD start PASS, portrait full journeys running',flush=True)
 deadline=time.monotonic()+240
 while any(p.poll() is None for _,_,p,_ in processes) and time.monotonic()<deadline:time.sleep(1)
 for label,serial,p,handle in processes:
  if p.poll() is None:p.kill()
  handle.close();text=(OUT/(label+'-portrait-instrumentation.txt')).read_text(encoding='utf8',errors='replace');passed='OK (4 tests)' in text;results['phones'][label]['portrait_full_pass']=passed;print(label,text[-900:],flush=True)
  if not passed:raise AssertionError(label+' portrait full integration failed')
 for label,serial in PHONES.items():
  run(serial,'shell','am','force-stop',PKG);run(serial,'shell','settings','put','system','user_rotation','1')
  p=subprocess.run([ADB,'-s',serial,'shell','am','instrument','-w','-r','-e','class','com.xmilo_at_your_side.sign_to_speak.PhonePreviewIntegrationTest',RUNNER],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=170)
  text=p.stdout.decode('utf8','replace');(OUT/(label+'-landscape-camera-instrumentation.txt')).write_text(text,encoding='utf8');passed='OK (1 test)' in text;results['phones'][label]['landscape_camera_pass']=passed;print(label,'landscape',text[-500:],flush=True)
  if not passed:raise AssertionError(label+' landscape preview failed')
finally:
 for label,serial in PHONES.items():
  uid={'s24':'10416','seeker':'10387'}[label]
  logs=run(serial,'logcat','-b','main','-d','--uid='+uid,'-T',results['phones'].get(label,{}).get('logStart','10-24 00:00:00.000'),'-s','PhonePreviewQA:I','UiObject2:D','TestRunner:I','Camera2CameraImpl:D','PreviewView:D','*:S')
  (OUT/(label+'-native-runtime.log')).write_text(logs,encoding='utf8')
  run(serial,'shell','input','keyevent','KEYCODE_HOME');run(serial,'shell','am','force-stop',PKG)
  for k,v in original.get(serial,{}).items():run(serial,'shell','settings','put','system',k,v)
 (OUT/'phone-final-verification.json').write_text(json.dumps(results,indent=2),encoding='utf8')
