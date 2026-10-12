#!/usr/bin/env python3
import json,pathlib,re,sys
QA='com.xmilo_at_your_side.sign_to_speak.qa'
def forbidden(path,kind=''):
    name=path.rsplit(':',1)[-1].lower()
    local_lint=(path==':react-native-safe-area-context:prepareLintJarForPublish' and kind in (
        'com.android.build.gradle.internal.tasks.PrepareLintJarForPublish',
        'com.android.build.gradle.internal.tasks.PrepareLintJarForPublish_Decorated'))
    return ('validatesigning' in name or 'keytool' in name or 'keystore' in name
        or re.search(r'(create|generate).*key',name) or name.startswith(('sign','install','uninstall','connected'))
        or ('publish' in name and not local_lint) or 'upload' in name or name in ('assemblerelease','bundlerelease')
        or 'ValidateSigningTask' in kind or 'PublishToMaven' in kind or 'PublishToIvyRepository' in kind)
def verify(text):
    records=re.findall(r'^SIGN_EMULATOR_UNSIGNED_GRAPH=(.+)$',text,re.M)
    assert len(records)==1, 'Require one resolved unsigned-variant guard result'
    record=json.loads(records[0]);assert record['agp']=='8.12.0'
    assert record['dryRun'] is True and record['defaultDebugFallback'] is False
    variants=record['variants'];assert variants['dsl'] is True
    for role,package in [('app',QA),('test',QA+'.test')]:
        assert variants[role]=={'applicationId':package,'signingConfig':None,'signingReady':False}
    tasks=set(re.findall(r'^(:[\w:.-]+) SKIPPED\s*$',text,re.M))
    expected={':app:verifyUnsignedEmulatorQaIsolation',':app:assembleEmulatorQa',':app:assembleEmulatorQaAndroidTest'}
    assert expected <= tasks, 'Dry-run output lacks expected exact tasks'
    assert expected <= {t['path'] for t in record['tasks']}
    assert not any(forbidden(t['path'],t['type']) for t in record['tasks']), 'Forbidden task class or name'
    types={t['path']:t['type'] for t in record['tasks']}
    assert tasks <= types.keys(), 'Every dry-run task needs matching inspected type metadata'
    assert not any(forbidden(t,types[t]) for t in tasks), 'Forbidden task name'
    assert not any(re.search(r':(?:package|assemble)(?:Debug|Qa)(?:$|AndroidTest)',t) for t in tasks), 'Unexpected unrelated APK variant'
    print('Resolved app/test are unsigned; graph excludes actual signing/key/device/release tasks.')
if __name__=='__main__':verify(pathlib.Path(sys.argv[1]).read_text())
