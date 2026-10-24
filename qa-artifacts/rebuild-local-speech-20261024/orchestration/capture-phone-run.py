import subprocess,pathlib,xml.etree.ElementTree as E,json,sys,time
ADB=r'C:\Users\1thea\AppData\Local\Android\Sdk\platform-tools\adb.exe'
PKG='com.xmilo_at_your_side.sign_to_speak.qa'
OUT=pathlib.Path('java-check/capture-qa/out')
def cmd(serial,*args,timeout=30):
 p=subprocess.run([ADB,'-s',serial,*args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
 s=p.stdout.decode('utf-8','replace')
 if p.returncode:raise RuntimeError(s)
 return s

def ui(serial):
 path='/data/local/tmp/sign-capture-qa-window.xml'
 try:
  cmd(serial,'shell','uiautomator','dump',path)
  raw=cmd(serial,'shell','cat',path)
  root=E.fromstring(raw)
  return [dict(n.attrib) for n in root.iter('node') if n.get('package') in [PKG+'.capture',PKG+'.tone','com.android.systemui','com.google.android.permissioncontroller','com.samsung.android.permissioncontroller','com.android.permissioncontroller']]
 finally:cmd(serial,'shell','rm','-f',path)

def tap(serial,bounds):
 import re
 x1,y1,x2,y2=map(int,re.findall(r'\d+',bounds));assert x2>x1 and y2>y1
 return cmd(serial,'shell','input','tap',str((x1+x2)//2),str((y1+y2)//2))
if __name__=='__main__':
 serial=sys.argv[1]
 if len(sys.argv)>2 and sys.argv[2]=='start':
  orig={k:cmd(serial,'shell','settings','get','system',k).strip() for k in ['accelerometer_rotation','user_rotation']}
  (OUT/(serial+'-rotation.json')).write_text(json.dumps(orig),encoding='utf8')
  cmd(serial,'shell','settings','put','system','accelerometer_rotation','0')
  cmd(serial,'shell','settings','put','system','user_rotation','0')
  cmd(serial,'shell','am','force-stop',PKG)
  cmd(serial,'shell','am','force-stop',PKG+'.tone')
  cmd(serial,'shell','am','force-stop',PKG+'.capture')
  cmd(serial,'shell','am','start','-n',PKG+'.capture/.RecorderActivity')
 print(json.dumps(ui(serial),ensure_ascii=False))
