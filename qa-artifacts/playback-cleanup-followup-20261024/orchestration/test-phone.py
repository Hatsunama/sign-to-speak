import pathlib,subprocess,json,time,sys,re
ROOT=pathlib.Path.cwd(); SHA=sys.argv[1]; LABEL=sys.argv[2]
SERIAL={'s24':'R3CX90HWPGB','seeker':'SM02G4061962301'}[LABEL]
OUT=ROOT/('java-check/cleanup-'+SHA[:7]+'-phone')
ADB=r'C:\Users\1thea\AppData\Local\Android\Sdk\platform-tools\adb.exe'
PKG='com.xmilo_at_your_side.sign_to_speak.qa'; COMP=PKG+'/com.xmilo_at_your_side.sign_to_speak.MainActivity'; RUNNER=PKG+'.test/androidx.test.runner.AndroidJUnitRunner'
receipt={'source':SHA,'phone':LABEL,'serial':SERIAL}; original={}; claimed=False

def run(*args,timeout=45):
 p=subprocess.run([ADB,'-s',SERIAL,*map(str,args)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
 s=p.stdout.decode('utf8','replace')
 if p.returncode: raise RuntimeError(s)
 return s

def foreground():
 text=run('shell','dumpsys','activity','activities')
 line=next((x for x in text.splitlines() if 'topResumedActivity=' in x),'')
 m=re.search(r'u[0-9]+ ([A-Za-z0-9_.]+)/',line)
 return m.group(1) if m else None

home=run('shell','cmd','package','resolve-activity','--brief','-a','android.intent.action.MAIN','-c','android.intent.category.HOME').splitlines()[-1].split('/')[0].strip()
current=foreground();receipt['readiness']={'foreground':current,'home':home}
if not current or current!=home:
 receipt['blocked']='Device has another foreground app or readiness is unknown; no device action taken.'
 (OUT/(LABEL+'-verification.json')).write_text(json.dumps(receipt,indent=2),encoding='utf8')
 print(json.dumps(receipt),flush=True);sys.exit(3)
try:
 claimed=True
 receipt['runtime']={'sdk':run('shell','getprop','ro.build.version.sdk').strip(),'page_size':run('shell','getconf','PAGESIZE').strip(),'model':run('shell','getprop','ro.product.model').strip()}
 original={k:run('shell','settings','get','system',k).strip() for k in ('accelerometer_rotation','user_rotation')}
 receipt['original_rotation']=original
 run('shell','settings','put','system','accelerometer_rotation','0');run('shell','settings','put','system','user_rotation','0')
 signing=json.loads((OUT/'signing-receipt.json').read_text()); expected={a['role']:a['signed_sha256'] for a in signing['artifacts']}
 installed={}
 for role,pkg in [('app',PKG),('test',PKG+'.test')]:
  assert 'Success' in run('install','-r','-t',OUT/(role+'-signed.apk'))
  path=run('shell','pm','path',pkg).strip().removeprefix('package:')
  assert path.startswith('/data/app/') and path.endswith('/base.apk')
  installed[role]=run('shell','sha256sum',path).split()[0];assert installed[role]==expected[role]
 receipt['installed_signed_sha256']=installed
 run('shell','am','force-stop',PKG)
 cold=run('shell','am','start','-W','-n',COMP)
 assert 'Status: ok' in cold and 'LaunchState: COLD' in cold
 receipt['cold_start']=cold
 print(LABEL+': exact hashes and COLD start verified; running five portrait integration tests',flush=True)
 text=run('shell','am','instrument','-w','-r','-e','class','com.xmilo_at_your_side.sign_to_speak.PhoneSpeechIntegrationTest,com.xmilo_at_your_side.sign_to_speak.PhonePreviewIntegrationTest',RUNNER,timeout=300)
 (OUT/(LABEL+'-portrait-instrumentation.txt')).write_text(text,encoding='utf8')
 receipt['portrait_pass']='OK (5 tests)' in text
 print(text[-1200:],flush=True);assert receipt['portrait_pass'],LABEL+' portrait tests failed'
 run('shell','am','force-stop',PKG);run('shell','settings','put','system','user_rotation','1')
 text=run('shell','am','instrument','-w','-r','-e','class','com.xmilo_at_your_side.sign_to_speak.PhonePreviewIntegrationTest',RUNNER,timeout=180)
 (OUT/(LABEL+'-landscape-camera-instrumentation.txt')).write_text(text,encoding='utf8')
 receipt['landscape_pass']='OK (1 test)' in text
 print(text[-900:],flush=True);assert receipt['landscape_pass'],LABEL+' landscape camera failed'
except BaseException as e:
 receipt['failure']=str(e)
 raise
finally:
 if claimed:
  run('shell','am','force-stop',PKG)
  run('shell','am','force-stop',PKG+'.test')
  for k,v in original.items():run('shell','settings','put','system',k,v)
  current=foreground()
  if current in (PKG,PKG+'.test',home):run('shell','input','keyevent','KEYCODE_HOME')
  receipt['final_foreground']=foreground();receipt['returned_home']=receipt['final_foreground']==home
  (OUT/(LABEL+'-verification.json')).write_text(json.dumps(receipt,indent=2),encoding='utf8')
  print(json.dumps(receipt),flush=True)
