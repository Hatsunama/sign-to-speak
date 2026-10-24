import pathlib,json,re,hashlib
out=pathlib.Path('java-check/capture-qa/out');summary={}
for label in ('s24-4ec7c8a','seeker-4ec7c8a'):
 text=(out/(label+'-final-timing.log')).read_text(encoding='utf-8-sig')
 lines=text.splitlines(); ph={}
 def ts(l):return float(l.split()[0])
 for name in ('CONTROL','PROTECTED'):
  start=next(l for l in lines if 'phase start name='+name in l)
  end=next(l for l in lines if 'phase end name='+name in l)
  data={k:int(v) for k,v in re.findall(r'(elapsedMs|durationMs|samples|nonzero|peak|readErrors)=(\d+)',end)}
  data.update(logStartSeconds=ts(start),logEndSeconds=ts(end))
  assert data['samples']>data['durationMs']*16*.9
  assert data['readErrors']==0
  assert 'phase-timeout' in end
  if name=='CONTROL':assert data['nonzero']>0 and data['peak']>0
  else:assert data['nonzero']==0 and data['peak']==0
  ph[name]=data
 tone=next(l for l in lines if 'tone end' in l)
 head=int(re.search(r'headFrames=(\d+)',tone)[1]);duration=int(re.search(r'durationMs=(\d+)',tone)[1])/1000
 assert head>0 and ts(tone)>ph['CONTROL']['logStartSeconds'] and ts(tone)-duration<ph['CONTROL']['logEndSeconds']
 begin=next(l for l in lines if 'I TestRunner: started: realFlite' in l)
 finish=next(l for l in lines if 'I TestRunner: finished: realFlite' in l)
 assert ph['PROTECTED']['logStartSeconds']<ts(begin)<ts(finish)<ph['PROTECTED']['logEndSeconds']
 result=(out/(label+'-protected-instrumentation.txt')).read_text(encoding='utf8');assert 'OK (1 test)' in result
 assert not any(x in text for x in ('cleanup failed','phase end failed','phase start failed','projection-revoked'))
 summary[label]={'source':'4ec7c8a38d9444db4d6ef2b3506faa27b0b5a292','appSignedSha256':'6f5d30e481781e5b7cc5dca13f47550143277ac159e27f84b5a01846afa3f7e1','phases':ph,'controlToneHeadFrames':head,'productionTestStartSeconds':ts(begin),'productionTestEndSeconds':ts(finish),'protectedTestPassed':True,'noMicOrScreenPixelsOrPcmPersisted':True,'controlIsolation':'Product QA force-stopped before CONTROL; only owned tone stimulus. Tone force-stopped before PROTECTED. Both phases limited to exact product/tone UIDs.','evidence':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [out/(label+'-final-timing.log'),out/(label+'-final-capture.log'),out/(label+'-protected-instrumentation.txt')]}}
(out/'final-capture-4ec7c8a-verification.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
print('Both phones: capture statistics valid; positive control; production test wholly inside protected window.')
