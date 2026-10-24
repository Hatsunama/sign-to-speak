import sys,importlib,pathlib,json,time,subprocess
m=importlib.import_module('capture-phone-run')
s=sys.argv[1];label=sys.argv[2];share=sys.argv[3]
out=m.OUT;pkg=m.PKG
signing=json.loads(pathlib.Path('java-check/rebuild-4ec7c8a-phone/signing-receipt.json').read_text(encoding='utf8'))
receipt={'serial':s,'source':signing['source'],'appSignedSha256':next(a['signed_sha256'] for a in signing['artifacts'] if a['role']=='app')}
try:
 installed=m.cmd(s,'shell','pm','path',pkg).strip().removeprefix('package:')
 assert m.cmd(s,'shell','sha256sum',installed).split()[0]==receipt['appSignedSha256']
 receipt['logStart']=m.cmd(s,'shell',"date '+%m-%d %H:%M:%S.000'").strip()
 m.tap(s,share)
 m.cmd(s,'shell','am','start','-n',pkg+'.tone/.ToneActivity')
 nodes=m.ui(s)
 play=[n for n in nodes if n['package']==pkg+'.tone' and n['class']=='android.widget.Button' and 'PLAY' in n['text'].upper()]
 assert len(play)==1,[(n['text'],n['bounds']) for n in nodes if n['text']]
 receipt['playButton']=play[0]
 def phase(name):return m.cmd(s,'shell','am','broadcast','--receiver-foreground','-n',pkg+'.capture/.PhaseReceiver','-a',pkg+'.capture.PHASE','--es','phase',name)
 phase('CONTROL');m.tap(s,play[0]['bounds']);print('CONTROL started',flush=True)
 time.sleep(21)
 m.cmd(s,'shell','am','force-stop',pkg+'.tone')
 phase('PROTECTED')
 time.sleep(0.35)
 receipt['instrumentStartUptime']=float(m.cmd(s,'shell','cat','/proc/uptime').split()[0])
 print('PROTECTED started; real speech instrumentation running',flush=True)
 result=m.cmd(s,'shell','am','instrument','-w','-r','-e','class','com.xmilo_at_your_side.sign_to_speak.PhoneSpeechIntegrationTest#realFlitePcmProtectedPlaybackAndInputBoundaries',pkg+'.test/androidx.test.runner.AndroidJUnitRunner',timeout=55)
 receipt['instrumentEndUptime']=float(m.cmd(s,'shell','cat','/proc/uptime').split()[0])
 (out/(label+'-protected-instrumentation.txt')).write_text(result,encoding='utf8')
 time.sleep(max(0,21-(receipt['instrumentEndUptime']-receipt['instrumentStartUptime'])))
 receipt['stop']=phase('STOP')
 logs=m.cmd(s,'logcat','-d','-T',receipt['logStart'],'-s','CaptureQA:I','ToneQA:I','*:S')
 (out/(label+'-final-capture.log')).write_text(logs,encoding='utf8')
 receipt['testPassed']='OK (1 test)' in result
 print(logs,flush=True);print(result[-400:],flush=True)
finally:
 for p in [pkg+'.tone',pkg+'.capture',pkg]:
  m.cmd(s,'shell','am','force-stop',p)
 m.cmd(s,'shell','input','keyevent','KEYCODE_HOME')
 original=json.loads((out/(s+'-rotation.json')).read_text(encoding='utf8'))
 for k,v in original.items():m.cmd(s,'shell','settings','put','system',k,v)
 (out/(label+'-final-capture-receipt.json')).write_text(json.dumps(receipt,indent=2),encoding='utf8')
