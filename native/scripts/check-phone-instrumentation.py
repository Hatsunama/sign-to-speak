#!/usr/bin/env python3
"""Audit the unsigned phone QA instrumentation APK before local signing."""
import importlib.util
import hashlib
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('phone_qa', HERE / 'check-phone-qa.py')
phone_qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phone_qa)
require = phone_qa.require
ANDROID = phone_qa.ANDROID
QA = phone_qa.PACKAGE
RUNNER = 'androidx.test.runner.AndroidJUnitRunner'
PERMISSION = 'android.permission.REORDER_TASKS'
LICENSE_DIR = HERE.parent / 'vendor/mediapipe-offline/licenses/sdk-licenses'
LICENSE_ASSETS = {
    'assets/sdk-licenses/mediapipe-tasks-1.1.1-NOTICE': LICENSE_DIR / 'mediapipe-tasks-1.1.1-NOTICE',
    'assets/sdk-licenses/Apache-2.0-LICENSE': LICENSE_DIR / 'Apache-2.0-LICENSE',
    'assets/sdk-licenses/litert-1.4.2-LICENSE': LICENSE_DIR / 'litert-1.4.2-LICENSE',
}
LICENSE_SHA256 = {
    'mediapipe-tasks-1.1.1-NOTICE': '8db2ce1a7fb4fa13d2261bcc4cd1f1c3044d94b59fb0e4b35a5c0b4ecb6a56d2',
    'Apache-2.0-LICENSE': 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30',
    'litert-1.4.2-LICENSE': '71c6915d04265772a0339bed47276942c678b45cc01534210ebe6984fd1aec65',
}


def verify(manifest, apk):
    phone_qa.validate_instrumentation_manifest(manifest)
    with phone_qa.open_unsigned_apk(apk, signature_message='Unexpected v1 signature') as (archive, names):
        for name, source in LICENSE_ASSETS.items():
            expected = LICENSE_SHA256[source.name]
            source_bytes = source.read_bytes()
            require(hashlib.sha256(source_bytes).hexdigest() == expected, f'Changed pinned SDK license source: {source}')
            require(name in names, f'Missing SDK license asset: {name}')
            require(archive.read(name) == source_bytes, f'Changed SDK license asset: {name}')
        libraries = [name for name in names if name.startswith('lib/') and name.endswith('.so')]
        require(all(name.split('/')[1] == 'arm64-v8a' for name in libraries), 'Instrumentation must use only arm64 native libraries')
    print('Unsigned phone QA instrumentation package and manifest verified.')


if __name__ == '__main__':
    verify(sys.argv[1], sys.argv[2])
