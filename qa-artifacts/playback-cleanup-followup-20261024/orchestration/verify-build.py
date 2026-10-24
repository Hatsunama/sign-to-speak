import pathlib,subprocess,json,zipfile,hashlib,struct,xml.etree.ElementTree as E,sys,importlib.util
ROOT=pathlib.Path.cwd(); SHA=sys.argv[1]; RUN=sys.argv[2]; REPO='Hatsunama/sign-to-speak'; OUT=ROOT/('java-check/cleanup-'+SHA[:7]+'-phone'); EV=ROOT/('java-check/cleanup-'+SHA[:7]+'-evidence');OUT.mkdir(exist_ok=True);EV.mkdir(exist_ok=True)
SDK=pathlib.Path(r'C:\Users\1thea\AppData\Local\Android\Sdk');JAVA=pathlib.Path(r'C:\Program Files\Eclipse Adoptium\jdk-17.0.18.8-hotspot\bin\java.exe');BT=SDK/'build-tools/36.0.0'
def run(*args,ok=0):
 p=subprocess.run([str(a) for a in args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180);s=p.stdout.decode('utf8','replace')
 if p.returncode!=ok:raise RuntimeError(s)
 return s
state=json.loads(run('gh','run','view',RUN,'--repo',REPO,'--json','status,conclusion,headSha'))
assert state=={'conclusion':'success','headSha':SHA,'status':'completed'},state
for name,d in [('sign-phone-qa-UNSIGNED-arm64-'+SHA,OUT),('sign-android-evidence-'+SHA+'-1',EV)]:run('gh','run','download',RUN,'--repo',REPO,'-n',name,'-D',d)
xmls=list(EV.glob('**/test-results/testDebugUnitTest/TEST-*.xml'));assert len(xmls)==7
counts={k:sum(int(E.parse(p).getroot().get(k,'0')) for p in xmls) for k in ('tests','failures','errors','skipped')};assert counts==dict(tests=122,failures=0,errors=0,skipped=0),counts
assert '344 checks passed' in next(EV.glob('**/sign-evidence/flite-jni.log')).read_text()
manifestdir=next(EV.glob('**/sign-evidence/phone-qa'))
artifacts=[]
for role,glob in [('app','qa/*.apk'),('test','androidTest/qa/*.apk')]:
 paths=list(OUT.glob(glob));assert len(paths)==1,paths
 p=paths[0]; manifest=manifestdir/('manifest.xml' if role=='app' else 'test-manifest.xml')
 verifier=ROOT/'native/scripts'/('check-phone-qa.py' if role=='app' else 'check-phone-instrumentation.py')
 run(sys.executable,verifier,manifest,p)
 unsigned=run(JAVA,'-Xmx128m','-jar',BT/'lib/apksigner.jar','verify','--verbose','--print-certs',p,ok=1)
 (OUT/(role+'-unsigned-verification.txt')).write_text(unsigned,encoding='utf8')
 actual=run(BT/'aapt2.exe','dump','xmltree',p,'--file','AndroidManifest.xml');(OUT/(role+'-actual-manifest.txt')).write_text(actual,encoding='utf8')
 assert 'android.permission.INTERNET' not in actual
 assert 'testOnly(0x01010272)=true' in actual
 with zipfile.ZipFile(p) as z:
  names=z.namelist();assert len(names)==len(set(names)) and z.testzip() is None
  libs=[n for n in names if n.startswith('lib/') and n.endswith('.so')];assert all(n.startswith('lib/arm64-v8a/') for n in libs)
  loads=[]
  for n in libs:
   data=z.read(n);assert data[:5]==b'\x7fELF\x02'
   pos=struct.unpack_from('<Q',data,32)[0]; size,num=struct.unpack_from('<HH',data,54)
   aligns=[struct.unpack_from('<Q',data,pos+i*size+48)[0] for i in range(num) if struct.unpack_from('<I',data,pos+i*size)[0]==1];assert aligns and min(aligns)>=16384
   zi=z.getinfo(n);off=zi.header_offset
   with p.open('rb') as f:f.seek(off+26);nl,el=struct.unpack('<HH',f.read(4))
   assert (off+30+nl+el)%16384==0 and zi.compress_type==zipfile.ZIP_STORED
   loads.append({'path':n,'load_alignments':aligns})
  if role=='app':
   assert len(libs)==14 and 'lib/arm64-v8a/libprivate_tts.so' in libs and 'assets/index.android.bundle' in names
   for filename in ('THIRD_PARTY_NOTICES.txt','MODIFICATIONS.txt'):
    matches=[n for n in names if n.endswith('/'+filename)];assert len(matches)==1,matches
    assert z.read(matches[0])==(ROOT/'native/licenses/flite'/filename).read_bytes()
 artifacts.append({'role':role,'path':str(p.relative_to(ROOT)),'unsigned_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size,'native_libraries':loads})
receipt={'tested_source':SHA,'ci_run':int(RUN),'android_unit':counts,'junit_xmls':len(xmls),'artifacts':artifacts}
(OUT/'artifact-verification.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
print(json.dumps(receipt))
