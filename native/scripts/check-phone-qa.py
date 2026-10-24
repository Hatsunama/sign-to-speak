#!/usr/bin/env python3
"""Audit an unsigned, isolated arm64 APK before local development signing."""
import pathlib
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
ANDROID='{http://schemas.android.com/apk/res/android}'
PACKAGE='com.xmilo_at_your_side.sign_to_speak.qa'

def verify(manifest, apk):
    root=ET.parse(manifest).getroot()
    assert root.get('package')==PACKAGE, 'APK must use the isolated QA identity'
    sdk=root.find('uses-sdk');assert sdk is not None
    assert sdk.get(ANDROID+'minSdkVersion')=='26' and sdk.get(ANDROID+'targetSdkVersion')=='36'
    requests=[p for p in root if p.tag in ('uses-permission','uses-permission-sdk-23','uses-permission-sdk-m')]
    internal=PACKAGE+'.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION'
    assert all(p.tag=='uses-permission' for p in requests)
    assert len(requests)==2 and {p.get(ANDROID+'name') for p in requests}=={'android.permission.CAMERA',internal}
    declarations=root.findall('permission')
    assert len(declarations)==1 and declarations[0].get(ANDROID+'name')==internal
    assert declarations[0].get(ANDROID+'protectionLevel') in ('signature','0x2','0x00000002')
    apps=root.findall('application');assert len(apps)==1
    app=apps[0]
    assert app.get(ANDROID+'allowAudioPlaybackCapture')=='false'
    assert app.get(ANDROID+'testOnly')=='true'
    assert app.get(ANDROID+'debuggable') in (None,'false')
    assert app.get(ANDROID+'allowBackup')=='false' and app.get(ANDROID+'usesCleartextTraffic')=='false'
    with zipfile.ZipFile(apk) as archive:
        names=archive.namelist()
        assert archive.testzip() is None, 'APK ZIP CRC failed'
        assert 'AndroidManifest.xml' in names and 'classes.dex' in names
        assert 'assets/index.android.bundle' in names and archive.getinfo('assets/index.android.bundle').file_size > 0, 'App must carry its nonempty bundled JavaScript'
        assert not any(re.match(r'META-INF/[^/]+\.(RSA|DSA|EC|SF)$',name,re.I) for name in names), 'Unexpected v1 signature material'
        libraries=[name for name in names if name.startswith('lib/') and name.endswith('.so')]
        assert libraries and {name.split('/')[1] for name in libraries}=={'arm64-v8a'}, 'APK must contain only arm64 native libraries'
        central=archive.start_dir
    # APK Signature Scheme v2/v3 use this footer immediately before the ZIP directory.
    with open(apk,'rb') as file:
        if central>=16:
            file.seek(central-16)
            assert file.read(16)!=b'APK Sig Block 42', 'Unexpected APK signing block'
    print('Unsigned arm64-only, test-only QA APK identity and manifest verified.')
if __name__=='__main__':verify(sys.argv[1],sys.argv[2])
