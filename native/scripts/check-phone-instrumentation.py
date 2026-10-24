#!/usr/bin/env python3
"""Audit the unsigned phone QA instrumentation APK before local signing."""
import importlib.util
import pathlib
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('phone_qa', HERE / 'check-phone-qa.py')
phone_qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phone_qa)
ANDROID = phone_qa.ANDROID
QA = phone_qa.PACKAGE
RUNNER = 'androidx.test.runner.AndroidJUnitRunner'
PERMISSION = 'android.permission.REORDER_TASKS'


def verify(manifest, apk):
    root = ET.parse(manifest).getroot()
    assert root.tag == 'manifest' and root.get('package') == QA + '.test', 'Wrong instrumentation package'
    sdks = root.findall('uses-sdk')
    assert len(sdks) == 1, 'Expected one SDK declaration'
    assert sdks[0].get(ANDROID + 'minSdkVersion') == '26'
    assert sdks[0].get(ANDROID + 'targetSdkVersion') == '36'
    runners = root.findall('instrumentation')
    assert len(runners) == 1, 'Expected one instrumentation runner'
    assert runners[0].get(ANDROID + 'name') == RUNNER
    assert runners[0].get(ANDROID + 'targetPackage') == QA
    apps = root.findall('application')
    assert len(apps) == 1 and apps[0].get(ANDROID + 'testOnly') == 'true'
    assert apps[0].get(ANDROID + 'debuggable') in (None, 'false')
    assert not any(apps[0].findall(tag) for tag in ('service', 'receiver', 'provider')), 'Unexpected instrumentation application component'
    requests = [p for p in root if p.tag in ('uses-permission', 'uses-permission-sdk-23', 'uses-permission-sdk-m')]
    assert len(requests) == 1 and requests[0].tag == 'uses-permission'
    assert requests[0].attrib == {ANDROID + 'name': PERMISSION}, 'Unexpected instrumentation permission'
    assert not root.findall('permission'), 'Instrumentation cannot declare custom permissions'

    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names)), 'Duplicate APK ZIP entry'
        assert archive.testzip() is None, 'APK ZIP CRC failed'
        assert 'AndroidManifest.xml' in names and 'classes.dex' in names
        assert not any(re.match(r'META-INF/[^/]+\.(RSA|DSA|EC|SF)$', name, re.I) for name in names), 'Unexpected v1 signature'
        libraries = [name for name in names if name.startswith('lib/') and name.endswith('.so')]
        assert all(name.split('/')[1] == 'arm64-v8a' for name in libraries), 'Instrumentation must use only arm64 native libraries'
        central = archive.start_dir
    # APK Signature Scheme v2/v3 footer sits immediately before ZIP central directory.
    with open(apk, 'rb') as file:
        if central >= 16:
            file.seek(central - 16)
            assert file.read(16) != b'APK Sig Block 42', 'Unexpected APK signing block'
    print('Unsigned phone QA instrumentation package and manifest verified.')


if __name__ == '__main__':
    verify(sys.argv[1], sys.argv[2])
