#!/usr/bin/env python3
"""Audit an unsigned, isolated arm64 APK before local development signing."""
import pathlib
import re
import sys
from contextlib import contextmanager
import xml.etree.ElementTree as ET
import zipfile
ANDROID='{http://schemas.android.com/apk/res/android}'
PACKAGE='com.xmilo_at_your_side.sign_to_speak.qa'
LICENSE_ROOT=pathlib.Path(__file__).resolve().parents[1]/'licenses'
REQUIRED_FLITE_ASSETS=('THIRD_PARTY_NOTICES.txt','MODIFICATIONS.txt')
REQUIRED_APPLICATION_ASSETS=('LICENSE','NOTICE')

def require(condition, message='APK validation failed'):
    if not condition:
        raise AssertionError(message)


def require_unique_names(names):
    require(len(names) == len(set(names)), 'Duplicate APK ZIP entry')


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
        for filename in REQUIRED_FLITE_ASSETS:
            source=(LICENSE_ROOT/'flite'/filename).read_bytes()
            entry='assets/flite/'+filename
            require(source, f'Flite license source is empty: {filename}')
            require(entry in names and archive.read(entry)==source, f'Missing or changed Flite license asset: {entry}')
        for filename in REQUIRED_APPLICATION_ASSETS:
            source=(LICENSE_ROOT/'sign-to-speak'/filename).read_bytes()
            entry='assets/sign-to-speak/'+filename
            require(source, f'Application license source is empty: {filename}')
            require(entry in names and archive.read(entry)==source, f'Missing or changed application license asset: {entry}')
        libraries=[name for name in names if name.startswith('lib/') and name.endswith('.so')]
        require(libraries and {name.split('/')[1] for name in libraries}=={'arm64-v8a'}, 'APK must contain only arm64 native libraries')
        require('lib/arm64-v8a/libprivate_tts.so' in libraries, 'APK must carry the local synthesis library')
    print('Unsigned arm64-only, test-only QA APK identity and manifest verified.')
if __name__=='__main__':verify(sys.argv[1],sys.argv[2])
