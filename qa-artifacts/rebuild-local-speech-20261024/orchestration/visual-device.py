import subprocess,pathlib,xml.etree.ElementTree as E,json,sys,re
ADB=r'C:\Users\1thea\AppData\Local\Android\Sdk\platform-tools\adb.exe';PKG='com.xmilo_at_your_side.sign_to_speak.qa';VIS=PKG+'.visualcapture';OUT=pathlib.Path('java-check/visual-capture-qa/out')
def cmd(s,*a,timeout=35):
 p=subprocess.run([ADB,'-s',s,*a],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout);assert p.returncode==0,p.stdout.decode('utf8','replace');return p.stdout.decode('utf8','replace')
def ui(s):
 path='/data/local/tmp/sign-visual-qa-window.xml'
 try:cmd(s,'shell','uiautomator','dump',path);return E.fromstring(cmd(s,'shell','cat',path))
 finally:cmd(s,'shell','rm','-f',path)
def rect(n):return list(map(int,re.findall(r'\d+',n.get('bounds',''))))
def tap(s,b):
 x1,y1,x2,y2=map(int,re.findall(r'\d+',b));assert x2>x1 and y2>y1;cmd(s,'shell','input','tap',str((x1+x2)//2),str((y1+y2)//2))
def show(s):
 allow=[VIS,PKG,'com.android.systemui','com.android.permissioncontroller','com.google.android.permissioncontroller','com.samsung.android.permissioncontroller']
 return [(n.get('package'),n.get('text'),n.get('bounds'),n.get('resource-id'),n.get('class')) for n in ui(s).iter('node') if n.get('package') in allow and (n.get('text') or n.get('clickable')=='true' or n.get('class')=='android.widget.Spinner')]
if __name__=='__main__':
 s=sys.argv[1]
 if len(sys.argv)>2 and sys.argv[2]=='start':
  orig={k:cmd(s,'shell','settings','get','system',k).strip() for k in ('accelerometer_rotation','user_rotation')};(OUT/(s+'-rotation.json')).write_text(json.dumps(orig),encoding='utf8')
  cmd(s,'shell','settings','put','system','accelerometer_rotation','0');cmd(s,'shell','settings','put','system','user_rotation','0');cmd(s,'shell','am','force-stop',PKG);cmd(s,'shell','am','force-stop',VIS);cmd(s,'shell','am','start','-n',VIS+'/.ConsentActivity')
 print(show(s))
