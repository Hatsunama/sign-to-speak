#!/usr/bin/env python3
"""Audit an unsigned, isolated arm64 APK before local development signing."""
import pathlib
import hashlib
import json
import re
import sys
from contextlib import contextmanager
import xml.etree.ElementTree as ET
import zipfile
ANDROID='{http://schemas.android.com/apk/res/android}'
PACKAGE='com.xmilo_at_your_side.sign_to_speak.qa'
LICENSE_ROOT=pathlib.Path(__file__).resolve().parents[1]/'licenses'
NATIVE=LICENSE_ROOT.parent
POCKET_ROOT=NATIVE/'generated'/'pocket-assets'
POCKET_MODEL={
    'model.safetensors':(111728528,'599329dcea2f2a8d17b575e1c6e11b78822453c9073040d55c26faadf4883188'),
    'tokenizer.model':(59339,'d461765ae179566678c93091c5fa6f2984c31bbe990bf1aa62d92c64d91bc3f6'),
    'alba.safetensors':(4163992,'11554cbe9f3d3a3e10722932df9952bb44928e534f7af3200524659166b5627a'),
}
REQUIRED_APPLICATION_ASSETS=('LICENSE','NOTICE')
REQUIRED_FRAMEWORK_ASSETS=('react-native-LICENSE.txt',
                           'react-native-safe-area-context-LICENSE.txt',
                           'ndk-libcxx-NOTICE.txt', 'ndk-libcxxabi-NOTICE.txt',
                           'double-conversion-LICENSE.txt', 'glog-COPYING.txt',
                           'fast_float-LICENSE-MIT.txt', 'folly-LICENSE.txt',
                           'hermes-dtoa.c-NOTICE.txt', 'hermes-g_fmt.c-NOTICE.txt',
                           'fresco-ijg-README.txt', 'fresco-ijg-ACKNOWLEDGMENT.txt',
                           'bolts-tasks-LICENSE.txt', 'old-facebook-MIT.txt',
                           'camera-libyuv-LICENSE.txt')
FRAMEWORK_SHA256={
    'react-native-LICENSE.txt':'da6d3703ed11cbe42bd212c725957c98da23cbff1998c05fa4b3d976d1a58e93',
    'react-native-safe-area-context-LICENSE.txt':'f5c7a4d527258e11fdfe3d84eb73cd14fe6d64224ef051e59f6ee21fe378800c',
    'ndk-libcxx-NOTICE.txt':'3f3d1da78246d5c2ffc879f8239d6e856b19ba813b8349cbe48f85bd018ee3af',
    'ndk-libcxxabi-NOTICE.txt':'b043b7c5fdaf8df24b2c3b50f7651e6acfc8bdd73cc152ec5d090b8a7e0ede33',
    'double-conversion-LICENSE.txt':'4af93c12062c58058378de2397dc1c92bbff9ddfb1d583a01c84127557ce97ca',
    'glog-COPYING.txt':'0fc497129c5c69ff6f22da6933c7e4aaef082fde8437fd57680c2780100772a4',
    'fast_float-LICENSE-MIT.txt':'e562f3f974ced7e69dd1db77b820b36bcf8f30377f1aa105723fba449c53c4e6',
    'folly-LICENSE.txt':'2206c00af7013581ae0d7c8c8a0089f02fab621913daac019b32336bd4d1e1db',
    'hermes-dtoa.c-NOTICE.txt':'45a0b19676f406b6aaeb9ebc04961ba711d38b13003a15b38ff7174b2568b0c4',
    'hermes-g_fmt.c-NOTICE.txt':'be5981b1b47d165581f00f9915679ecd19a4fa44ba259f5b60ee1fd1be1df31f',
    'fresco-ijg-README.txt':'4b7b9f8c03bb8d60270dfd12684e70ab21e4abfd27e73905cd1a7c4cae6f5cdb',
    'fresco-ijg-ACKNOWLEDGMENT.txt':'d903a69ab39425283b44e2ea355cbfaf79bcd738b69c2b1c3c700ce80f2d89f9',
    'bolts-tasks-LICENSE.txt':'9e69750c616877af2dec86f961a53e39324c3655e25b1d6ceed06e9661ec072b',
    'old-facebook-MIT.txt':'52412d7bc7ce4157ea628bbaacb8829e0a9cb3c58f57f99176126bc8cf2bfc85',
    'camera-libyuv-LICENSE.txt':'2b2cc1180c7e6988328ad2033b04b80117419db9c4c584918bbb3cfec7e9364f',
}

def require(condition, message='APK validation failed'):
    if not condition:
        raise AssertionError(message)


def require_unique_names(names):
    require(len(names) == len(set(names)), 'Duplicate APK ZIP entry')


def is_retired_speech_artifact(name):
    """Match retired Flite paths and libraries without rejecting TensorFlow Lite assets."""
    parts=name.lower().split('/')
    basename=parts[-1]
    return ('flite' in parts or basename=='libprivate_tts.so' or
            bool(re.fullmatch(r'libflite[^/]*\.so',basename)))


def verify_pocket_assets(archive,names):
    require(not any(name.lower().startswith('assets/flite/') for name in names), 'Retired Flite notices must not ship')
    actual={name for name in names if name.startswith('assets/pocket-alba/') and not name.endswith('/')}
    expected={'assets/pocket-alba/'+name for name in POCKET_MODEL}
    require(actual==expected, 'APK Pocket model asset set differs from pinned Alba set')
    for name,(size,sha) in POCKET_MODEL.items():
        entry='assets/pocket-alba/'+name
        require(archive.getinfo(entry).file_size==size, 'Pocket asset size mismatch: '+name)
        digest=hashlib.sha256()
        with archive.open(entry) as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):
                digest.update(block)
        require(digest.hexdigest()==sha, 'Pocket asset SHA-256 mismatch: '+name)
    notice_root=POCKET_ROOT/'pocket-licenses'
    require(not notice_root.is_symlink() and notice_root.is_dir(), 'Prepared Pocket notices missing')
    sources=[p for p in notice_root.rglob('*') if p.is_file()]
    require(sources and all(not p.is_symlink() for p in sources), 'Linked or empty Pocket notice bundle')
    expected_notices={'assets/pocket-licenses/'+p.relative_to(notice_root).as_posix() for p in sources}
    actual_notices={name for name in names if name.startswith('assets/pocket-licenses/') and not name.endswith('/')}
    require(actual_notices==expected_notices, 'APK Pocket notices differ from prepared notice inventory')
    for source in sources:
        entry='assets/pocket-licenses/'+source.relative_to(notice_root).as_posix()
        require(archive.read(entry)==source.read_bytes(), 'Pocket notice changed in APK: '+entry)
    routes=json.loads((notice_root/'cargo-declaration-routes.json').read_text())
    require(routes.get('status')=='declarations-selected-for-component-probe' and
            len(routes.get('packages',[]))==133 and not routes.get('blocked_packages'),
            'Incomplete Cargo declaration routes')


def verify_application_assets(archive, names):
    for filename in REQUIRED_APPLICATION_ASSETS:
        source=(LICENSE_ROOT/'sign-to-speak'/filename).read_bytes()
        entry='assets/sign-to-speak/'+filename
        require(source, f'Application license source is empty: {filename}')
        require(entry in names and archive.read(entry)==source,
                f'Missing or changed application license asset: {entry}')
    template=(LICENSE_ROOT/'react-native-template-MIT.txt').read_bytes()
    require('assets/react-native-template-MIT.txt' in names and
            archive.read('assets/react-native-template-MIT.txt')==template,
            'Missing or changed React Native template notice')
    for name in REQUIRED_FRAMEWORK_ASSETS:
        source=(LICENSE_ROOT/'framework'/name).read_bytes()
        entry='assets/framework/'+name
        require(hashlib.sha256(source).hexdigest()==FRAMEWORK_SHA256[name],
                f'Framework license source differs from reviewed bytes: {name}')
        require(entry in names, f'Missing framework license asset: {entry}')
        require(source and archive.read(entry)==source,
                f'Missing or changed framework license asset: {entry}')
    expected={'assets/framework/'+name for name in REQUIRED_FRAMEWORK_ASSETS}
    actual={name for name in names if name.startswith('assets/framework/') and not name.endswith('/')}
    require(actual==expected, 'APK framework license asset set differs from reviewed inventory')


def validate_app_manifest(manifest, identity_message='APK must use the isolated QA identity'):
    root=ET.parse(manifest).getroot()
    require(root.get('package')==PACKAGE, identity_message)
    sdks=root.findall('uses-sdk');require(len(sdks)==1, 'Expected one SDK declaration')
    sdk=sdks[0]
    require(sdk.get(ANDROID+'minSdkVersion')=='26' and sdk.get(ANDROID+'targetSdkVersion')=='36')
    requests=[p for p in root if p.tag in ('uses-permission','uses-permission-sdk-23','uses-permission-sdk-m')]
    internal=PACKAGE+'.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION'
    require(all(p.tag=='uses-permission' for p in requests))
    require(len(requests)==2 and {p.get(ANDROID+'name') for p in requests}=={'android.permission.CAMERA',internal})
    declarations=root.findall('permission')
    require(len(declarations)==1 and declarations[0].get(ANDROID+'name')==internal)
    require(declarations[0].get(ANDROID+'protectionLevel') in ('signature','0x2','0x00000002'))
    apps=root.findall('application');require(len(apps)==1)
    app=apps[0]
    require(app.get(ANDROID+'allowAudioPlaybackCapture')=='false')
    require(app.get(ANDROID+'testOnly')=='true')
    require(app.get(ANDROID+'debuggable') in (None,'false'))
    require(app.get(ANDROID+'allowBackup')=='false' and app.get(ANDROID+'usesCleartextTraffic')=='false')
    return root


def validate_instrumentation_manifest(manifest, emulator=False):
    root=ET.parse(manifest).getroot()
    require(root.tag == 'manifest' and root.get('package') == PACKAGE + '.test', 'Wrong instrumentation package')
    sdks=root.findall('uses-sdk')
    require(len(sdks)==1, 'Expected one SDK declaration')
    require(sdks[0].get(ANDROID+'minSdkVersion')=='26')
    require(sdks[0].get(ANDROID+'targetSdkVersion')=='36')
    runners=root.findall('instrumentation')
    require(len(runners)==1, 'Expected one instrumentation runner')
    require(runners[0].get(ANDROID+'name')=='androidx.test.runner.AndroidJUnitRunner')
    require(runners[0].get(ANDROID+'targetPackage')==PACKAGE)
    apps=root.findall('application')
    require(len(apps)==1 and apps[0].get(ANDROID+'testOnly')=='true',
            'Expected one test-only instrumentation application' if emulator else 'APK validation failed')
    require(apps[0].get(ANDROID+'debuggable') in (None,'false'),
            'Instrumentation cannot be debuggable' if emulator else 'APK validation failed')
    require(not any(apps[0].findall(tag) for tag in ('service','receiver','provider')), 'Unexpected instrumentation application component')
    requests=[p for p in root if p.tag in ('uses-permission','uses-permission-sdk-23','uses-permission-sdk-m')]
    require(len(requests)==1 and requests[0].tag=='uses-permission')
    require(requests[0].attrib=={ANDROID+'name':'android.permission.REORDER_TASKS'}, 'Unexpected instrumentation permission')
    require(not root.findall('permission'),
            'Instrumentation cannot define custom permission authority' if emulator else 'Instrumentation cannot declare custom permissions')
    return root


@contextmanager
def open_unsigned_apk(apk, signature_message='Unexpected v1 signature material',
                      footer_message='Unexpected APK signing block', crc_message='APK ZIP CRC failed'):
    with zipfile.ZipFile(apk) as archive:
        names=archive.namelist()
        require_unique_names(names)
        require(archive.testzip() is None, crc_message)
        require('AndroidManifest.xml' in names and 'classes.dex' in names)
        require(not any(re.match(r'META-INF/[^/]+\.(RSA|DSA|EC|SF)$',name,re.I) for name in names), signature_message)
        central=archive.start_dir
        with open(apk,'rb') as file:
            if central>=16:
                file.seek(central-16)
                require(file.read(16)!=b'APK Sig Block 42', footer_message)
        yield archive,names


def verify(manifest, apk):
    validate_app_manifest(manifest)
    with open_unsigned_apk(apk) as (archive,names):
        require('assets/index.android.bundle' in names and archive.getinfo('assets/index.android.bundle').file_size > 0, 'App must carry its nonempty bundled JavaScript')
        verify_pocket_assets(archive,names)
        verify_application_assets(archive,names)
        libraries=[name for name in names if name.startswith('lib/') and name.endswith('.so')]
        require(libraries and {name.split('/')[1] for name in libraries}=={'arm64-v8a'}, 'APK must contain only arm64 native libraries')
        require('lib/arm64-v8a/libsign_pocket_tts_runtime.so' in libraries,
                'APK must carry the Pocket Rust runtime')
        require('lib/arm64-v8a/libprivate_pocket_tts.so' in libraries,
                'APK must carry the Pocket JNI bridge')
        require('lib/arm64-v8a/libc++_shared.so' in libraries,
                'APK must carry the C++ runtime for the Pocket bridge')
        require(not any(is_retired_speech_artifact(name) for name in libraries),
                'Retired speech runtime must not ship')
    print('Unsigned arm64-only, test-only QA APK identity and manifest verified.')
if __name__=='__main__':verify(sys.argv[1],sys.argv[2])
