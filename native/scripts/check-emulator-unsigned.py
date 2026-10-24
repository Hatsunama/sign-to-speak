#!/usr/bin/env python3
"""Audit unsigned binary APKs before the separately authorized local signing step."""
import importlib.util,pathlib,re,sys,xml.etree.ElementTree as ET,zipfile
HERE=pathlib.Path(__file__).parent
spec=importlib.util.spec_from_file_location('phone',HERE/'check-phone-qa.py')
phone=importlib.util.module_from_spec(spec);spec.loader.exec_module(phone)
ANDROID=phone.ANDROID
QA=phone.PACKAGE

def unsigned(apk,expected_abi=None):
    with zipfile.ZipFile(apk) as archive:
        names=archive.namelist();assert archive.testzip() is None
        assert 'AndroidManifest.xml' in names and 'classes.dex' in names
        assert not any(re.match(r'META-INF/[^/]+\.(RSA|DSA|EC|SF)$',n,re.I) for n in names)
        libs=[n for n in names if n.startswith('lib/') and n.endswith('.so')]
        if expected_abi:
            assert libs and {n.split('/')[1] for n in libs}=={expected_abi}
            assert 'assets/index.android.bundle' in names and archive.getinfo('assets/index.android.bundle').file_size > 0, 'App must carry its nonempty bundled JavaScript'
        else: assert not libs or {n.split('/')[1] for n in libs}=={'x86_64'}
        central=archive.start_dir
    with open(apk,'rb') as file:
        if central>=16:
            file.seek(central-16);assert file.read(16)!=b'APK Sig Block 42', 'APK is already signed'

def verify(app_xml,app_apk,test_xml,test_apk):
    # Reuse the exact phone manifest contract; only ABI and bundled-JS requirements differ.
    root=ET.parse(app_xml).getroot()
    assert root.get('package')==QA
    sdk=root.find('uses-sdk');assert sdk is not None
    assert sdk.get(ANDROID+'minSdkVersion')=='26' and sdk.get(ANDROID+'targetSdkVersion')=='36'
    req=[p for p in root if p.tag in ('uses-permission','uses-permission-sdk-23','uses-permission-sdk-m')]
    internal=QA+'.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION'
    assert len(req)==2 and all(p.tag=='uses-permission' for p in req)
    assert {p.get(ANDROID+'name') for p in req}=={'android.permission.CAMERA',internal}
    declarations=root.findall('permission')
    assert len(declarations)==1 and declarations[0].get(ANDROID+'name')==internal
    assert declarations[0].get(ANDROID+'protectionLevel') in ('signature','0x2','0x00000002')
    apps=root.findall('application');assert len(apps)==1
    app=apps[0]
    assert app.get(ANDROID+'allowAudioPlaybackCapture')=='false'
    assert app.get(ANDROID+'testOnly')=='true' and app.get(ANDROID+'debuggable') in (None,'false')
    assert app.get(ANDROID+'allowBackup')=='false' and app.get(ANDROID+'usesCleartextTraffic')=='false'
    unsigned(app_apk,'x86_64')
    test=ET.parse(test_xml).getroot()
    assert test.get('package')==QA+'.test'
    runners=test.findall('instrumentation')
    assert len(runners)==1 and runners[0].get(ANDROID+'targetPackage')==QA
    assert runners[0].get(ANDROID+'name')=='androidx.test.runner.AndroidJUnitRunner'
    assert test.find('application').get(ANDROID+'testOnly')=='true'
    requests=[p for p in test if p.tag in ('uses-permission','uses-permission-sdk-23','uses-permission-sdk-m')]
    assert all(p.tag=='uses-permission' for p in requests)
    # Pinned androidx.test.core 1.7.0 contributes only this normal ActivityScenario permission.
    # It belongs to the isolated test package, never to the app's CAMERA-only contract.
    assert len(requests)==1 and requests[0].attrib=={ANDROID+'name':'android.permission.REORDER_TASKS'}
    assert not test.findall('permission'), 'Instrumentation cannot define custom permission authority'
    test_sdk=test.find('uses-sdk');assert test_sdk is not None
    assert test_sdk.get(ANDROID+'minSdkVersion')=='26' and test_sdk.get(ANDROID+'targetSdkVersion')=='36'
    unsigned(test_apk)
    print('Unsigned, isolated, test-only x86_64 app/instrumentation pair verified; no runtime test yet.')

if __name__=='__main__':verify(*sys.argv[1:])
