#!/usr/bin/env python3
"""Audit unsigned binary APKs before the separately authorized local signing step."""
import importlib.util,pathlib,sys
HERE=pathlib.Path(__file__).parent
spec=importlib.util.spec_from_file_location('phone',HERE/'check-phone-qa.py')
phone=importlib.util.module_from_spec(spec);spec.loader.exec_module(phone)
require=phone.require
require_unique_names=phone.require_unique_names
ANDROID=phone.ANDROID
QA=phone.PACKAGE

def unsigned(apk,expected_abi=None):
    with phone.open_unsigned_apk(apk, signature_message='APK validation failed',
                                 footer_message='APK is already signed',
                                 crc_message='APK validation failed') as (archive,names):
        libs=[n for n in names if n.startswith('lib/') and n.endswith('.so')]
        if expected_abi:
            require(libs and {n.split('/')[1] for n in libs}=={expected_abi})
            require('assets/index.android.bundle' in names and archive.getinfo('assets/index.android.bundle').file_size > 0, 'App must carry its nonempty bundled JavaScript')
            phone.verify_pocket_assets(archive,names)
            phone.verify_application_assets(archive,names)
            require('lib/'+expected_abi+'/libsign_pocket_tts_runtime.so' in libs)
            require('lib/'+expected_abi+'/libprivate_pocket_tts.so' in libs)
            require('lib/'+expected_abi+'/libc++_shared.so' in libs)
            require(not any(phone.is_retired_speech_artifact(name) for name in libs))
        else: require(not libs or {n.split('/')[1] for n in libs}=={'x86_64'})

def verify(app_xml,app_apk,test_xml,test_apk):
    phone.validate_app_manifest(app_xml, identity_message='APK validation failed')
    unsigned(app_apk,'x86_64')
    phone.validate_instrumentation_manifest(test_xml, emulator=True)
    unsigned(test_apk)
    print('Unsigned, isolated, test-only x86_64 app/instrumentation pair verified; no runtime test yet.')

if __name__=='__main__':verify(*sys.argv[1:])
