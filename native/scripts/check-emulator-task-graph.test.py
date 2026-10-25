import importlib.util,json,pathlib,unittest,subprocess,tempfile,os,sys
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
 def test_exact_local_lint_jar_is_not_network_publication(self):
  r=record();path=':react-native-safe-area-context:prepareLintJarForPublish'
  r['tasks'].append({'path':path,'type':'com.android.build.gradle.internal.tasks.PrepareLintJarForPublish_Decorated'})
  m.verify(output(r)+'\n'+path+' SKIPPED')
  r['tasks'][-1]['type']='UntrustedReplacementTask'
  with self.assertRaises(AssertionError):m.verify(output(r)+'\n'+path+' SKIPPED')
  r['tasks'][-1]['type']='com.android.build.gradle.internal.tasks.PrepareLintJarForPublish_Decorated'
  r['tasks'][-1]['path']=':unrelated:prepareLintJarForPublish'
  with self.assertRaises(AssertionError):m.verify(output(r))
 def test_empty(self):
  with self.assertRaises(AssertionError):m.verify('BUILD SUCCESSFUL')
 def test_reject_forbidden_tasks(self):
  for task in ['validateSigningEmulatorQaAndroidTest','installEmulatorQa','connectedEmulatorQaAndroidTest','publish','assembleRelease','bundleRelease','assembleDebug','assembleQa','keytool','generateDebugKey','signApk']:
   r=record();r['tasks'].append({'path':':app:'+task,'type':'DefaultTask'})
   with self.subTest(task=task),self.assertRaises(AssertionError):m.verify(output(r)+'\n:app:'+task+' SKIPPED')
 def test_reject_signing_task_class(self):
  r=record();r['tasks'].append({'path':':app:innocentName','type':'com.android.build.gradle.internal.tasks.ValidateSigningTask_Decorated'})
  with self.assertRaises(AssertionError):m.verify(output(r))
 def test_reject_renamed_publishing_task_class(self):
  for kind in ['org.gradle.api.publish.maven.tasks.PublishToMavenRepository_Decorated','org.gradle.api.publish.ivy.tasks.PublishToIvyRepository_Decorated']:
   r=record();r['tasks'].append({'path':':app:innocentName','type':kind})
   with self.subTest(kind=kind),self.assertRaises(AssertionError):m.verify(output(r))
 def test_reject_default_signer(self):
  r=record();r['variants']['test']['signingConfig']='debug'
  with self.assertRaises(AssertionError):m.verify(output(r))
 def test_reject_ready(self):
  r=record();r['variants']['app']['signingReady']=True
  with self.assertRaises(AssertionError):m.verify(output(r))
 def test_reject_duplicate_inspected_path(self):
  r=record();r['tasks'].append({'path':TASKS[0],'type':'AnotherTask'})
  with self.assertRaises(AssertionError):m.verify(output(r))
 def test_reject_fallback_enabled(self):
  r=record();r['defaultDebugFallback']=True
  with self.assertRaises(AssertionError):m.verify(output(r))
class OptimizedCli(unittest.TestCase):
 def test_malformed_graph_rejected_with_optimization(self):
  for mode in ('flag','environment'):
   for mutation in ('forbidden_task','signed_test','wrong_variant','missing_dry_run_task'):
    r=record();text=output(r)
    if mutation=='forbidden_task':
     r['tasks'].append({'path':':app:validateSigningEmulatorQa','type':'DefaultTask'});text=output(r)
    elif mutation=='signed_test':
     r['variants']['test']['signingConfig']='debug';text=output(r)
    elif mutation=='wrong_variant':
     r['variants']['app']['applicationId']='wrong.package';text=output(r)
    else:
     text=text.replace(':app:assembleEmulatorQaAndroidTest SKIPPED','')
    with tempfile.TemporaryDirectory() as temp:
     path=pathlib.Path(temp)/'task-graph.txt';path.write_text(text,encoding='utf-8')
     command=[sys.executable]
     environment=os.environ.copy();environment.pop('PYTHONOPTIMIZE',None)
     if mode=='flag':command.append('-O')
     else:environment['PYTHONOPTIMIZE']='1'
     command.extend([str(p),str(path)])
     result=subprocess.run(command,env=environment,capture_output=True,text=True)
     with self.subTest(mode=mode,mutation=mutation):
      self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
