set -euo pipefail
umask 077
test ! -e /job/out/run
mkdir -p /job/out/run
exec > >(tee -a /job/out/run/job.log) 2>&1
exec python -B - <<'PY'
import os,sys,json,hashlib,tarfile,shutil,subprocess,datetime,time
from pathlib import Path,PurePosixPath
if not __debug__: raise RuntimeError('Integrity checks must be enabled')
out=Path('/job/out/run')
archive=Path('/job/source/holistic-offline-probe-source-20261120-r3.tar.gz')
assert archive.stat().st_size==466329
assert hashlib.sha256(archive.read_bytes()).hexdigest()=='a1198bf8704e64b27527476fe01cf5ed9a1db79b20faccbe7f3edf7b1f4736d9'
work=Path('/tmp/sign-to-speak-holistic-probe-r4');work.mkdir()
with tarfile.open(archive,'r:gz') as tar:
 members=tar.getmembers()
 assert len(members)==72 and sum(m.size for m in members)<4000000
 seen=set()
 for member in members:
  rel=PurePosixPath(member.name)
  assert rel.parts and not rel.is_absolute() and '..' not in rel.parts and rel not in seen and member.isfile()
  seen.add(rel)
 for member in members:
  target=work.joinpath(*PurePosixPath(member.name).parts);target.parent.mkdir(parents=True,exist_ok=True)
  with tar.extractfile(member) as src,target.open('xb') as dst:shutil.copyfileobj(src,dst)
manifest=work/'SOURCE-MANIFEST.json'
assert hashlib.sha256(manifest.read_bytes()).hexdigest()=='a2d6ba59cd75f7b96e3e6a576723566755d1f86c55eb1448ad462fd2238d809e'
for row in json.loads(manifest.read_bytes())['files']:
 payload=(work/row['file']).read_bytes();assert len(payload)==row['bytes'] and hashlib.sha256(payload).hexdigest()==row['sha256']
env={name:os.environ[name] for name in ('PATH','LD_LIBRARY_PATH','LANG','LC_ALL') if name in os.environ}
scratch=work/'scratch';scratch.mkdir()
env.update(TMPDIR=str(scratch),TEMP=str(scratch),TMP=str(scratch),PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1',PIP_PROGRESS_BAR='off',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
stage='dependencies'
def write_json(name,value):
 with (out/name).open('x',encoding='utf-8') as stream:json.dump(value,stream,indent=2,allow_nan=False);stream.write('\n')
try:
 stage='native_runtime_dependencies'
 env['DEBIAN_FRONTEND']='noninteractive'
 packages=['libegl1','libgles2','libxcb1','libx11-6','libxext6','libsm6','libxrender1','libgl1','libglib2.0-0','libportaudio2']
 subprocess.run(['apt-get','update'],check=True,env=env,cwd=work,timeout=240)
 subprocess.run(['apt-get','install','-y','--no-install-recommends',*packages],check=True,env=env,cwd=work,timeout=240)
 native=subprocess.run(['dpkg-query','-W','-f=$'+'{Package}=$'+'{Version}\n',*packages],check=True,env=env,capture_output=True,text=True)
 write_json('native-dependency-versions.json',{'packages':native.stdout.splitlines(),'cloud_environment_only':True})
 stage='pinned_python_dependencies'
 subprocess.run([sys.executable,'-m','pip','install','--no-cache-dir','--only-binary=:all:','--no-deps','--require-hashes','-r',str(work/'requirements-holistic-probe-cloud.txt'),'--report',str(out/'install-report.json')],check=True,env=env,cwd=work)
 dependencies=subprocess.run([sys.executable,'-m','pip','check'],env=env,cwd=work,capture_output=True,text=True,timeout=60)
 write_json('python-dependency-check.json',{'returncode':dependencies.returncode,'stdout':dependencies.stdout,'stderr':dependencies.stderr})
 assert dependencies.returncode==0,'Declared Python dependency closure is inconsistent'
 import importlib.metadata
 checks=[]
 for name in ('opencv-contrib-python','mediapipe'):
  distribution=importlib.metadata.distribution(name)
  libraries=[distribution.locate_file(path) for path in distribution.files if str(path).endswith('.so')]
  assert libraries,'Missing native binary inventory: '+name
  for library in libraries:
   linkage=subprocess.run(['ldd',str(library)],env=env,cwd=work,capture_output=True,text=True,timeout=30)
   checks.append({'distribution':name,'file':str(library.relative_to(distribution.locate_file(''))),'returncode':linkage.returncode,'stdout':linkage.stdout,'stderr':linkage.stderr})
 write_json('native-linkage-closure.json',{'libraries':checks})
 assert all(row['returncode']==0 and 'not found' not in row['stdout'] for row in checks),'A stock SDK binary has unresolved native runtime dependencies'
 stage='full_source_qa'
 sys.path.insert(0,str(work))
 from holistic_probe_runner import run_worker
 code="import sys,json,unittest,faulthandler;faulthandler.dump_traceback_later(60,repeat=True);sys.path.insert(0,"+repr(str(work))+");r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover("+repr(str(work))+"));print(json.dumps({'tests':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skips':len(r.skipped),'torch_imported':'torch' in sys.modules,'tensorflow_imported':'tensorflow' in sys.modules}),flush=True);sys.exit(not r.wasSuccessful() or r.testsRun!=428 or bool(r.skipped) or 'torch' in sys.modules or 'tensorflow' in sys.modules)"
 qa_dir=out/'source-qa';qa_dir.mkdir()
 qa=run_worker([sys.executable,'-B','-c',code],cwd=work,env=env,result_dir=work/'qa-unused-results',durable_dir=qa_dir,timeout=600)
 write_json('qa.json',{'outcome':qa,'source_archive_sha256':'a1198bf8704e64b27527476fe01cf5ed9a1db79b20faccbe7f3edf7b1f4736d9','required_tests':428})
 assert qa['status']=='exited' and qa['returncode']==0 and not qa['capture_errors'],'Required source/integration checks did not pass; no scan started'
 qa_summary=json.loads((qa_dir/'worker.stdout').read_text(encoding='utf-8').splitlines()[-1])
 assert qa_summary=={'tests':428,'failures':0,'errors':0,'skips':0,'torch_imported':False,'tensorflow_imported':False}
 print(json.dumps({'qa':qa_summary}),flush=True)
 stage='dependency_provenance'
 report=json.loads((out/'install-report.json').read_bytes())
 wheels=[row for row in report['install'] if row['metadata']['name'].casefold()=='mediapipe']
 assert len(wheels)==1 and wheels[0]['metadata']['version']=='1.1.1'
 assert wheels[0]['download_info']['archive_info']['hashes']['sha256']=='cbb54f6411d8d254f7151ff92cbfea29413be92f802be46a520cc7316319c3cc'
 stage='verified_local_inputs'
 sys.path.insert(0,str(work))
 from fsboard_task_admission import fetch_clip
 from admission_probe import fetch
 with tarfile.open(work/'inputs/fsboard-v4-fit-source-20261119-r3.tar.gz','r:gz') as frozen:
  proposal_bytes=frozen.extractfile('private-proposal.json').read()
 assert hashlib.sha256(proposal_bytes).hexdigest()=='6f04da3b218f664d8ab4097ab0212fdd135220f89dc8019e9b3b1fb04213e6d9'
 proposal=json.loads(proposal_bytes);clip=proposal['clips']['development'][0]
 assert clip['split']=='daun_v3-train' and clip['original_bytes']==509853
 local_clip=work/'staged-development.mp4'
 clip_bytes,clip_sha=fetch_clip(clip,'development',local_clip)
 assert clip_bytes==clip['original_bytes']
 model=work/'holistic_landmarker.task'
 with model.open('xb') as destination:
  model_bytes,model_sha=fetch('https://storage.googleapis.com/mediapipe-models/holistic_landmarker/holistic_landmarker/float16/1/holistic_landmarker.task',destination,13683609,13683609)
 assert model_bytes==13683609 and model_sha=='e2dab61191e2dcd0a15f943d8e3ed1dce13c82dfa597b9dd39f562975a50c3f8'
 write_json('staging-provenance.json',{'proposal_sha256':hashlib.sha256(proposal_bytes).hexdigest(),'development_roster_index':0,'publisher_split':clip['split'],'source_path':clip['source_path'],'source_expected_md5':clip['metadata_md5'],'clip_sha256':clip_sha,'clip_bytes':clip_bytes,'model_sha256':model_sha,'model_bytes':model_bytes,'worker_inherits_no_sockets_or_secrets':True})
 stage='offline_holistic_inference'
 resultdir=work/'holistic-result'
 command=[sys.executable,'-B',str(work/'holistic_video_probe.py'),'--clip',str(local_clip),'--clip-sha256',clip_sha,'--clip-bytes',str(clip_bytes),'--model',str(model),'--output',str(resultdir)]
 inference_dir=out/'inference';inference_dir.mkdir()
 outcome=run_worker(command,cwd=work,env=env,result_dir=resultdir,durable_dir=inference_dir,timeout=300)
 assert outcome['status']=='exited' and outcome['returncode']==0 and not outcome['capture_errors'],'Offline extraction did not pass; inspect preserved worker outcome'
 binding=outcome['artifacts'].get('worker-receipt.json');assert binding is not None
 payload=(inference_dir/'worker-receipt.json').read_bytes()
 assert len(payload)==binding['bytes'] and hashlib.sha256(payload).hexdigest()==binding['sha256']
 receipt=json.loads(payload);wall=outcome['wall_seconds']
 assert receipt['status']=='SUCCESS' and receipt['input']['clip']['sha256']==clip_sha
 assert receipt['runtime']['delegate']=='CPU' and receipt['runtime']['offline_guard']=='seccomp_direct_socket_barrier'
 assert receipt['frame_counts'].get('processed',0)>0
 write_json('child-exit.json',{'status':'source_checks_and_bounded_offline_extraction_passed','returncode':0,'tests_required':428,'source_archive_sha256':'a1198bf8704e64b27527476fe01cf5ed9a1db79b20faccbe7f3edf7b1f4736d9','worker_receipt_sha256':hashlib.sha256(payload).hexdigest(),'worker_receipt_bytes':len(payload),'frame_counts':receipt['frame_counts'],'coverage':receipt['coverage'],'wall_seconds':wall,'qwen_training_started':False,'recognition_or_android_parity_qualified':False})
 print(json.dumps({'status':'bounded_offline_extraction_finished','frame_counts':receipt['frame_counts'],'coverage':receipt['coverage'],'wall_seconds':wall}),flush=True)
except Exception as error:
 write_json('bootstrap-failure.json',{'status':'FAILED','stage':stage,'error_type':type(error).__name__})
 raise
PY
