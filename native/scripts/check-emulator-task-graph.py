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
def require(condition, message):
    if not condition:
        raise AssertionError(message)

def verify(text):
    records=re.findall(r'^SIGN_EMULATOR_UNSIGNED_GRAPH=(.+)$',text,re.M)
    require(len(records)==1, 'Require one resolved unsigned-variant guard result')
    try:
        record=json.loads(records[0])
    except json.JSONDecodeError as error:
        raise AssertionError('Malformed resolved unsigned-variant guard result') from error
    require(isinstance(record,dict), 'Malformed resolved unsigned-variant guard result')
    require(record.get('agp')=='8.12.0', 'Wrong AGP version')
    require(record.get('dryRun') is True and record.get('defaultDebugFallback') is False,
            'Graph must be a dry run without default debug signing fallback')
    variants=record.get('variants')
    require(isinstance(variants,dict) and variants.get('dsl') is True,
            'Unsigned variant DSL guard did not pass')
    for role,package in [('app',QA),('test',QA+'.test')]:
        require(variants.get(role)=={'applicationId':package,'signingConfig':None,'signingReady':False},
                f'Wrong unsigned {role} variant metadata')
    inspected=record.get('tasks')
    require(isinstance(inspected,list), 'Missing inspected task graph')
    for task in inspected:
        require(isinstance(task,dict) and isinstance(task.get('path'),str)
                and isinstance(task.get('type'),str), 'Malformed inspected task metadata')
    tasks=set(re.findall(r'^(:[\w:.-]+) SKIPPED\s*$',text,re.M))
    expected={':app:verifyUnsignedEmulatorQaIsolation',':app:assembleEmulatorQa',':app:assembleEmulatorQaAndroidTest'}
    require(expected <= tasks, 'Dry-run output lacks expected exact tasks')
    require(expected <= {task['path'] for task in inspected}, 'Inspected graph lacks expected exact tasks')
    require(not any(forbidden(task['path'],task['type']) for task in inspected),
            'Forbidden task class or name')
    types={task['path']:task['type'] for task in inspected}
    require(len(types)==len(inspected), 'Duplicate inspected task path')
    require(tasks <= types.keys(), 'Every dry-run task needs matching inspected type metadata')
    require(not any(forbidden(task,types[task]) for task in tasks), 'Forbidden task name')
    require(not any(re.search(r':(?:package|assemble)(?:Debug|Qa)(?:$|AndroidTest)',task)
                    for task in tasks), 'Unexpected unrelated APK variant')
    print('Resolved app/test are unsigned; graph excludes actual signing/key/device/release tasks.')
if __name__=='__main__':verify(pathlib.Path(sys.argv[1]).read_text())
