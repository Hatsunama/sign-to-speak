import sys,importlib,pathlib,json,time,subprocess,re,io,hashlib
from PIL import Image
from visual_overlay_bounds import exclude_visible_edge_panel, exclude_visible_system_bubble
m=importlib.import_module('visual-device');s=sys.argv[1];label=sys.argv[2];share=sys.argv[3];out=m.OUT;pkg=m.PKG;vis=m.VIS
signing=json.loads(pathlib.Path('java-check/rebuild-4ec7c8a-phone/signing-receipt.json').read_text());expected=next(a['signed_sha256'] for a in signing['artifacts'] if a['role']=='app');viewports=json.loads(pathlib.Path('java-check/rebuild-4ec7c8a-phone/ui-final-comparison.json').read_text())
receipt={'source':signing['source'],'appSignedSha256':expected,'label':label,'screenshots':{},'camera_used':False};crop=None;base_crop=None;viewport_right=None

def window(package,activity):
 raw=m.cmd(s,'shell','dumpsys','window',package).replace('\r','');blocks=re.findall(r'(?ms)^  Window #[^\n]*:\n.*?(?=^  Window #|^  mGlobalConfiguration|\Z)',raw)
 found=[b for b in blocks if package in b.splitlines()[0] and activity in b.splitlines()[0] and 'isVisible=true' in b];assert len(found)==1,'Visible owned window not unique'
 b=found[0];assert all(v in b for v in ('mHasSurface=true','HAS_DRAWN','isOnScreen=true')),'Owned window not drawn';return b

def screenshot(phase):
 global crop
 window_dump=m.cmd(s,'shell','dumpsys','window');input_dump=m.cmd(s,'shell','dumpsys','input')
 crop,edge=exclude_visible_edge_panel(base_crop,window_dump,viewport_right)
 crop,bubble=exclude_visible_system_bubble(crop,window_dump,input_dump,viewport_right)
 assert crop[2]-crop[0] > (base_crop[2]-base_crop[0])*.75,'System overlays would remove too much of broad app ROI'
 receipt['crop']=crop
 if edge or bubble:
  evidence={'original_crop':base_crop,'crop':crop}
  if edge:evidence['samsung_edge']=edge
  if bubble:evidence['systemui_bubbles']=bubble
  receipt.setdefault('systemOverlayExclusion',{})[phase]=evidence
  (out/(label+'-system-overlay-exclusion.json')).write_text(json.dumps(receipt['systemOverlayExclusion'],indent=2),encoding='utf8')
 p=subprocess.run([m.ADB,'-s',s,'exec-out','screencap','-p'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=25);assert p.returncode==0 and p.stdout.startswith(b'\x89PNG')
 image=Image.open(io.BytesIO(p.stdout)).convert('RGBA');part=image.crop(tuple(crop));colors=part.getcolors(maxcolors=part.width*part.height);assert colors is not None
 total=part.width*part.height;black=sum(n for n,c in colors if c==(0,0,0,255));magenta=sum(n for n,c in colors if c==(255,0,255,255))
 info={'crop':crop,'image_size':list(image.size),'pixels':total,'opaqueBlack':black,'magenta':magenta,'full_png_sha256':hashlib.sha256(p.stdout).hexdigest()};receipt['screenshots'][phase]=info
 if phase=='PROTECTED':
  part.save(out/(label+'-protected-ui-crop.png'));assert black==total,'Secure Home leaked nonblack pixels in OS screenshot crop'
 else:assert magenta>total*.99,'Screenshot control is not magenta'
 image.close();part.close()

def phase(name):
 args=['shell','am','broadcast','--receiver-foreground','-n',vis+'/.PhaseReceiver','-a',vis+'.PHASE','--es','phase',name]
 if name!='STOP':
  for key,value in zip(['left','top','right','bottom'],crop):args+=['--ei',key,str(value)]
 m.cmd(s,*args)
 if name=='STOP':return
 deadline=time.monotonic()+10
 while True:
  time.sleep(.5);logs=m.cmd(s,'logcat','-b','main','-d','-T',receipt['logStart'],'-s','VisualCaptureQA:I','*:S')
  if 'phase end name='+name+' ' in logs:return
  assert time.monotonic()<deadline,'Visual phase did not end: '+name

def control():
 m.cmd(s,'shell','am','start','-W','-n',vis+'/.ControlActivity');root=m.ui(s);bars=[m.rect(n)[3] for n in root.iter('node') if n.get('package')==vis and n.get('resource-id') in ['android:id/action_bar_container','android:id/action_bar']]
 return max(bars,default=0),window(vis,'ControlActivity')
try:
 path=m.cmd(s,'shell','pm','path',pkg).strip().removeprefix('package:');assert m.cmd(s,'shell','sha256sum',path).split()[0]==expected
 receipt['logStart']=m.cmd(s,'shell',"date '+%m-%d %H:%M:%S.000'").strip();m.tap(s,share)
 bar,before=control();vp=viewports[label]['scroll_viewport'];base_crop=[vp[0]+2,max(vp[1],bar)+2,vp[2]-2,vp[3]-2];viewport_right=vp[2];crop=base_crop;assert crop[0]<crop[2] and crop[1]<crop[3];receipt['crop']=crop
 (out/(label+'-control-window.txt')).write_text(before,encoding='utf8');screenshot('CONTROL');phase('CONTROL');print(label,'visual CONTROL complete',flush=True)
 m.cmd(s,'shell','am','force-stop',pkg);m.cmd(s,'shell','am','start','-W','-n',pkg+'/com.xmilo_at_your_side.sign_to_speak.MainActivity')
 root=m.ui(s);assert any(n.get('package')==pkg and n.get('text')=='Your hands. Your words.' for n in root.iter('node')),'Expected Home not shown'
 assert 'PrivateCameraView' not in m.cmd(s,'shell','dumpsys','activity',pkg+'/com.xmilo_at_your_side.sign_to_speak.MainActivity'),'Camera must remain off'
 assert 'mWakefulness=Awake' in m.cmd(s,'shell','dumpsys','power'),'Phone not awake'
 protected=window(pkg,'MainActivity');flags=re.search(r'\bfl=([0-9a-fA-F]+)\b',protected);assert (flags and int(flags[1],16)&0x2000) or re.search(r'\bSECURE\b',protected),'Secure flag not set'
 (out/(label+'-protected-window.txt')).write_text(protected,encoding='utf8');screenshot('PROTECTED');phase('PROTECTED');window(pkg,'MainActivity');print(label,'visual PROTECTED complete',flush=True)
 control();screenshot('CONTROL_AFTER');phase('CONTROL_AFTER');phase('STOP');receipt['complete']=True
finally:
 logs=m.cmd(s,'logcat','-b','main','-d','-T',receipt.get('logStart','10-24 00:00:00.000'),'-s','VisualCaptureQA:I','*:S');(out/(label+'-visual-capture.log')).write_text(logs,encoding='utf8')
 for p in [vis,pkg]:m.cmd(s,'shell','am','force-stop',p)
 m.cmd(s,'shell','input','keyevent','KEYCODE_HOME')
 original=json.loads((out/(s+'-rotation.json')).read_text(encoding='utf8'))
 for k,v in original.items():m.cmd(s,'shell','settings','put','system',k,v)
 (out/(label+'-visual-receipt.json')).write_text(json.dumps(receipt,indent=2),encoding='utf8')
 print(logs,flush=True)
