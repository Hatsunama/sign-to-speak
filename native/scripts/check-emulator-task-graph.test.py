import importlib.util,json,pathlib,unittest
p=pathlib.Path(__file__).with_name('check-emulator-task-graph.py')
s=importlib.util.spec_from_file_location('graph',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
TASKS=[':app:'+t for t in ['verifyUnsignedEmulatorQaIsolation','assembleEmulatorQa','assembleEmulatorQaAndroidTest']]
def record():return {'agp':'8.12.0','dryRun':True,'defaultDebugFallback':False,'variants':{'dsl':True,'app':{'applicationId':m.QA,'signingConfig':None,'signingReady':False},'test':{'applicationId':m.QA+'.test','signingConfig':None,'signingReady':False}},'tasks':[{'path':t,'type':'DefaultTask'} for t in TASKS]}
def output(r=None):return 'SIGN_EMULATOR_UNSIGNED_GRAPH='+json.dumps(record() if r is None else r)+'\n'+'\n'.join(t+' SKIPPED' for t in TASKS)
class Graph(unittest.TestCase):
 def test_valid(self):m.verify(output())
 def test_signing_config_versions_is_unsigned_metadata(self):
  r=record()
  for n in ['writeEmulatorQaSigningConfigVersions','writeEmulatorQaAndroidTestSigningConfigVersions']:r['tasks'].append({'path':':app:'+n,'type':'SigningConfigVersionsWriterTask'})
  m.verify(output(r)+'\n:app:writeEmulatorQaSigningConfigVersions SKIPPED\n:app:writeEmulatorQaAndroidTestSigningConfigVersions SKIPPED')
 def test_empty(self):
  with self.assertRaises(AssertionError):m.verify('BUILD SUCCESSFUL')
 def test_reject_forbidden_tasks(self):
  for task in ['validateSigningEmulatorQaAndroidTest','installEmulatorQa','connectedEmulatorQaAndroidTest','publish','assembleRelease','bundleRelease','assembleDebug','assembleQa','keytool','generateDebugKey','signApk']:
   with self.subTest(task=task),self.assertRaises(AssertionError):m.verify(output()+'\n:app:'+task+' SKIPPED')
 def test_reject_signing_task_class(self):
  r=record();r['tasks'].append({'path':':app:innocentName','type':'com.android.build.gradle.internal.tasks.ValidateSigningTask_Decorated'})
  with self.assertRaises(AssertionError):m.verify(output(r))
 def test_reject_default_signer(self):
  r=record();r['variants']['test']['signingConfig']='debug'
  with self.assertRaises(AssertionError):m.verify(output(r))
 def test_reject_ready(self):
  r=record();r['variants']['app']['signingReady']=True
  with self.assertRaises(AssertionError):m.verify(output(r))
 def test_reject_fallback_enabled(self):
  r=record();r['defaultDebugFallback']=True
  with self.assertRaises(AssertionError):m.verify(output(r))
if __name__=='__main__':unittest.main()
