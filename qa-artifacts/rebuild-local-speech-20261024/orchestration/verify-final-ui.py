import pathlib,json,subprocess,xml.etree.ElementTree as E,re,time,hashlib
ROOT=pathlib.Path.cwd();OUT=ROOT/'java-check/rebuild-4ec7c8a-phone';ADB=r'C:\Users\1thea\AppData\Local\Android\Sdk\platform-tools\adb.exe';PKG='com.xmilo_at_your_side.sign_to_speak.qa';COMP=PKG+'/com.xmilo_at_your_side.sign_to_speak.MainActivity';PHONES={'s24':'R3CX90HWPGB','seeker':'SM02G4061962301'}
base=json.loads((ROOT/'java-check/pre-rebuild-phone-ui.json').read_text());expected=json.loads((OUT/'signing-receipt.json').read_text());appsha=next(a['signed_sha256'] for a in expected['artifacts'] if a['role']=='app');results={}
def cmd(serial,*a):
 p=subprocess.run([ADB,'-s',serial,*a],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=30);assert p.returncode==0,p.stdout;return p.stdout.decode('utf8','replace')
def ui(serial):
 path='/data/local/tmp/sign-final-ui-qa.xml'
 try:cmd(serial,'shell','uiautomator','dump',path);return E.fromstring(cmd(serial,'shell','cat',path))
 finally:cmd(serial,'shell','rm','-f',path)
def shape(root):return [{k:n.get(k,'') for k in ['text','class','bounds','clickable','enabled','content-desc']} for n in root.iter('node') if n.get('package')==PKG]
def button(serial,root,label):
 parent={c:p for p in root.iter() for c in p};node=next(n for n in root.iter('node') if n.get('package')==PKG and n.get('text')==label)
 while node.get('clickable')!='true':node=parent[node]
 x1,y1,x2,y2=map(int,re.findall(r'\d+',node.get('bounds')));assert x2>x1 and y2>y1
 cmd(serial,'shell','input','tap',str((x1+x2)//2),str((y1+y2)//2))
old=subprocess.check_output(['git','show','10bc6fa8d4d5baa4e44c3fcebd75b2d675ba3b07:native/App.tsx']);new=subprocess.check_output(['git','show','4ec7c8a38d9444db4d6ef2b3506faa27b0b5a292:native/App.tsx']);marker=b'  return <View style={styles.screen}>'
assert old[old.index(marker):]==new[new.index(marker):];uihash=hashlib.sha256(new[new.index(marker):]).hexdigest()
for label,serial in PHONES.items():
 original={k:cmd(serial,'shell','settings','get','system',k).strip() for k in ('accelerometer_rotation','user_rotation')}
 try:
  path=cmd(serial,'shell','pm','path',PKG).strip().removeprefix('package:');assert cmd(serial,'shell','sha256sum',path).split()[0]==appsha
  cmd(serial,'shell','settings','put','system','accelerometer_rotation','0');cmd(serial,'shell','settings','put','system','user_rotation','0');cmd(serial,'shell','am','force-stop',PKG);cold=cmd(serial,'shell','am','start','-W','-n',COMP);assert 'LaunchState: COLD' in cold
  end=time.monotonic()+20
  while True:
   root=ui(serial)
   if any(n.get('text')=='Your hands. Your words.' for n in root.iter('node')):break
   assert time.monotonic()<end,'Home never mounted'
  assert shape(root)==base[serial]['home'],label+' home hierarchy differs'
  button(serial,root,'Hatsu Is Here For You');thanks=ui(serial);assert shape(thanks)==base[serial]['thanks'],label+' Thank you hierarchy differs'
  button(serial,thanks,'Back to Home');returned=ui(serial);assert shape(returned)==base[serial]['returned'],label+' returned hierarchy differs'
  dump=cmd(serial,'shell','dumpsys','activity',COMP);assert 'PrivateCameraView' not in dump
  rawWindow=cmd(serial,'shell','dumpsys','window',PKG).replace('\r','')
  block=re.search(r'(?ms)^  Window #[^\n]*'+re.escape(COMP)+r'[^\n]*:\n.*?(?=^  Window #|^  mGlobalConfiguration|\Z)',rawWindow)
  assert block,'Target window unavailable'
  window=block.group(0);flagMatch=re.search(r'\bfl=([0-9a-fA-F]+)\b',window)
  assert (flagMatch is not None and int(flagMatch.group(1),16)&0x2000) or re.search(r'\bSECURE\b',window),'Target secure flag missing'
  assert all(v in window for v in ['mHasSurface=true','HAS_DRAWN','isOnScreen=true','isVisible=true']),'Target window not actually drawn/visible'
  (OUT/(label+'-secure-window.txt')).write_text(window,encoding='utf8')
  scroll=next(n for n in returned.iter('node') if n.get('package')==PKG and n.get('scrollable')=='true')
  viewport=list(map(int,re.findall(r'\d+',scroll.get('bounds'))))
  results[label]={'scroll_viewport':viewport,'app_signed_sha256':appsha,'home_exact_hierarchy':True,'thanks_exact_hierarchy':True,'return_exact_hierarchy':True,'camera_off':True,'secure_window_verified':True,'screenshots_taken':False,'original_rotation':original,'canonical_home_return_and_styles_sha256':uihash}
 finally:
  cmd(serial,'shell','input','keyevent','KEYCODE_HOME');cmd(serial,'shell','am','force-stop',PKG)
  for k,v in original.items():cmd(serial,'shell','settings','put','system',k,v)
(OUT/'ui-final-comparison.json').write_text(json.dumps(results,indent=2),encoding='utf8');print(json.dumps(results))
