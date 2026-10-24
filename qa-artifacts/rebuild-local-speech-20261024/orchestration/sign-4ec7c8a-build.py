import pathlib,json,subprocess,zipfile,hashlib,os,shutil
ROOT=pathlib.Path.cwd();OUT=ROOT/'java-check/rebuild-4ec7c8a-phone';SDK=pathlib.Path(r'C:\Users\1thea\AppData\Local\Android\Sdk');BT=SDK/'build-tools/36.0.0';JDK=pathlib.Path(r'C:\Program Files\Eclipse Adoptium\jdk-17.0.18.8-hotspot\bin');KEY=pathlib.Path(r'C:\Users\1thea\.android\debug.keystore');CERT='2a0d37dc62eebed39bd08a9238c9255e26eb52a00143a02e0c6c473489985a21'
assert os.environ.get('SIGN_QA_STOREPASS')
def run(*args):
 p=subprocess.run([str(a) for a in args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=45);s=p.stdout.decode('utf8','replace')
 if p.returncode:raise RuntimeError(s)
 return s
keyBefore=hashlib.sha256(KEY.read_bytes()).hexdigest()
listing=run(JDK/'keytool.exe','-list','-v','-keystore',KEY,'-storepass:env','SIGN_QA_STOREPASS','-alias','androiddebugkey')
assert CERT in listing.lower().replace(':',''), 'Existing key certificate mismatch'
receipt=json.loads((OUT/'artifact-verification.json').read_text());signed=[]
for a in receipt['artifacts']:
 source=ROOT/a['path'];dest=OUT/(a['role']+'-signed.apk')
 if a['role']=='app' and a['byte_identical_to_0757343']:
  shutil.copyfile(ROOT/'java-check/rebuild-0757343-phone/app-signed.apk',dest)
 else:
  run(JDK/'java.exe','-Xmx128m','-jar',BT/'lib/apksigner.jar','sign','--ks',KEY,'--ks-key-alias','androiddebugkey','--ks-pass','env:SIGN_QA_STOREPASS','--key-pass','env:SIGN_QA_STOREPASS','--v1-signing-enabled','false','--v2-signing-enabled','true','--v3-signing-enabled','true','--v4-signing-enabled','false','--out',dest,source)
 verification=run(JDK/'java.exe','-Xmx128m','-jar',BT/'lib/apksigner.jar','verify','--verbose','--print-certs',dest);assert CERT in verification
 (OUT/(a['role']+'-signature-verification.txt')).write_text(verification,encoding='utf8')
 with zipfile.ZipFile(source) as z,zipfile.ZipFile(dest) as signedzip:
  assert z.namelist()==signedzip.namelist() and all(z.read(n)==signedzip.read(n) for n in z.namelist())
 run(BT/'zipalign.exe','-c','-P','16','4',dest)
 signed.append({'role':a['role'],'unsigned_sha256':a['unsigned_sha256'],'signed_sha256':hashlib.sha256(dest.read_bytes()).hexdigest(),'zip_payloads_identical':True,'zipalign_16k':True})
assert hashlib.sha256(KEY.read_bytes()).hexdigest()==keyBefore,'Existing signing key changed'
(OUT/'signing-receipt.json').write_text(json.dumps({'source':receipt['tested_source'],'certificate_sha256':CERT,'key_exported':False,'key_unchanged':True,'artifacts':signed},indent=2),encoding='utf8')
print(json.dumps(signed))
