import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, writeFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
const script = new URL('./ci-sdk.sh', import.meta.url).pathname;
function fixture({ndk = true, license = true} = {}) {
  const root = mkdtempSync(join(tmpdir(), 'sign-sdk-'));
  const put = (path, body='fixture', mode=0o644) => {
    mkdirSync(join(root,path,'..'), {recursive:true});
    writeFileSync(join(root,path),body,{mode});
  };
  put('platforms/android-36/android.jar');
  put('build-tools/36.0.0/aapt2', '#!/bin/sh\nexit 0\n', 0o755);
  put('cmake/3.31.5/bin/cmake', '#!/bin/sh\nexit 0\n', 0o755);
  if(ndk) put('ndk/27.1.12297006/source.properties', 'Pkg.Revision = 27.1.12297006\n');
  if(license) put('licenses/android-sdk-license');
  return {root,put,run:()=>spawnSync('bash',[script],{encoding:'utf8',env:{...process.env,ANDROID_HOME:root,ANDROID_SDK_ROOT:root,RUNNER_TEMP:root}}), cleanup:()=>rmSync(root,{recursive:true,force:true})};
}
test('uses installed exact prerequisites without installer',()=>{
  const f=fixture(); try { const r=f.run(); assert.equal(r.status,0,r.stderr); } finally {f.cleanup();}
});
test('missing accepted license fails before installer',()=>{
  const f=fixture({ndk:false,license:false}); try {const r=f.run(); assert.notEqual(r.status,0);assert.match(r.stderr,/existing.*license/i);}finally{f.cleanup();}
});
test('new license prompt is refused with EOF, never accepted',()=>{
  const f=fixture({ndk:false}); try {
    f.put('cmdline-tools/latest/bin/sdkmanager','#!/bin/bash\nread -r answer || exit 7\necho "unexpected acceptance: $answer" >&2\nexit 8\n',0o755);
    const r=f.run();assert.notEqual(r.status,0);assert.match(r.stderr,/new terms|license/i);assert.doesNotMatch(r.stderr,/unexpected acceptance/);
  }finally{f.cleanup();}
});
test('installs only exact NDK under existing accepted license',()=>{
  const f=fixture({ndk:false});try{
    f.put('cmdline-tools/latest/bin/sdkmanager',`#!/bin/bash\n[[ "$1" == --install && "$2" == 'ndk;27.1.12297006' && "$#" == 2 ]] || exit 9\nmkdir -p "$ANDROID_HOME/ndk/27.1.12297006"\necho 'Pkg.Revision = 27.1.12297006' > "$ANDROID_HOME/ndk/27.1.12297006/source.properties"\n`,0o755);
    const r=f.run();assert.equal(r.status,0,r.stderr);
  }finally{f.cleanup();}
});
