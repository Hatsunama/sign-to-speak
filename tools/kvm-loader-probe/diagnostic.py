#!/usr/bin/env python3
"""One read-only CLI/loader diagnosis; no AVD, ADB, KVM or apt operation."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'kvm-boot-probe'))
import probe

BRANCH = 'refs/heads/probe/sign-kvm-loader-20261013'
PARENT = 'ae2947458daa5f41b7330645a2432dd8d732d91c'


def policy(env):
    expected = dict(PROBE_REPOSITORY_PRIVATE='false', PROBE_REPOSITORY_VISIBILITY='public',
                    GITHUB_REPOSITORY='Hatsunama/sign-to-speak', GITHUB_ACTIONS='true',
                    RUNNER_ENVIRONMENT='github-hosted', GITHUB_EVENT_NAME='push',
                    GITHUB_REF=BRANCH, ImageOS='ubuntu24', RUNNER_OS='Linux', RUNNER_ARCH='X64')
    for key, value in expected.items():
        if env.get(key) != value:
            raise probe.Blocked('DIAGNOSTIC_POLICY:' + key)
    if not re.fullmatch(r'[a-f0-9]{40}', env.get('PROBE_COMMIT', '')):
        raise probe.Blocked('DIAGNOSTIC_POLICY:commit')


def classify(text):
    # Emit only library basenames and version identifiers, never arbitrary output.
    missing = re.findall(r'([A-Za-z0-9_+.-]+\.so(?:\.[A-Za-z0-9_.-]+)*)\s*=>\s*not found', text)
    missing += re.findall(r'error while loading shared libraries:\s*([A-Za-z0-9_+.-]+\.so(?:\.[A-Za-z0-9_.-]+)*):', text)
    versions = re.findall(r"version [`']([A-Za-z0-9_.-]+)['`] not found", text)
    return {'missing_libraries': sorted(set(missing)), 'missing_versions': sorted(set(versions))}


def classify_adb(text):
    patterns = {
        'socket-listener-failed': r'could not install|cannot bind|failed to bind',
        'address-in-use': r'address already in use',
        'permission-denied': r'permission denied|operation not permitted',
        'invalid-listener-argument': r'incompatible with|bad.*socket|invalid.*socket',
        'authentication-initialization-failed': r'failed to.*(?:key|auth)|unable to create key',
        'usb-initialization-failed': r'(?:usb|libusb).*(?:fail|error|abort)|(?:fail|error).*usb',
        'mdns-initialization-failed': r'(?:mdns|openscreen).*(?:fail|error|abort)|(?:fail|error).*mdns',
        'missing-file': r'no such file or directory',
        'unsupported-argument': r'unknown (?:option|command)|unrecognized|usage:',
    }
    return {'categories': [k for k, pattern in patterns.items() if re.search(pattern, text, re.I)],
            'source_locations': sorted(set(re.findall(r'\b[A-Za-z0-9_-]+\.(?:cpp|cc):[0-9]+', text))),
            'missing_libraries': classify(text)['missing_libraries']}


class NoDeviceLease:
    def acquire(self):
        return {'mode': 'diagnostic-only-no-kvm-access'}
    def release(self):
        pass


def main():
    policy(os.environ)
    for ref, expected in [('HEAD', os.environ['PROBE_COMMIT']), ('HEAD^', PARENT)]:
        if subprocess.check_output(['git', 'rev-parse', ref], text=True).strip() != expected:
            raise probe.Blocked('DIAGNOSTIC_SOURCE_MISMATCH')
    probe.PACKAGES = {'platform-tools': '37.0.1'}
    probe.TOTAL_SECONDS = 8 * 60
    root = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    evidence = root / 'sign-kvm-loader-evidence'
    evidence.mkdir(exist_ok=False)
    p = probe.Probe(evidence, root)
    p.kvm = NoDeviceLease()
    facts = {'scope': 'isolated-adb-startup-diagnostic-only', 'commit': os.environ['PROBE_COMMIT'], 'status': 'blocked'}
    def interrupted(signum, frame):
        raise probe.Blocked('INTERRUPTED:' + str(signum))
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        p.setup()
        p.stage = 'package-metadata'
        listing = p.run([p.sdkmanager, '--list', '--channel=0', '--sdk_root=' + str(p.sdk)], 'available.txt', seconds=90)
        probe.parse_available(listing)
        p.stage = 'platform-tools-installation'
        p.run([p.sdkmanager, '--channel=0', '--sdk_root=' + str(p.sdk), 'platform-tools'], 'sdk-install.txt', seconds=180)
        props = probe.properties(p.sdk / 'platform-tools/source.properties')
        if props.get('Pkg.Revision') != '37.0.1':
            raise probe.Blocked('PLATFORM_TOOLS_REVISION_DRIFT')
        before = json.loads((evidence / 'runner-license-marker-hashes.json').read_text())
        after = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in (p.sdk / 'licenses').glob('*') if f.is_file()}
        if before != after:
            raise probe.Blocked('LICENSE_MARKERS_CHANGED')
        p.stage = 'isolated-adb-startup'
        binary = str(p.sdk / 'platform-tools/adb')
        facts['adb_sha256'] = hashlib.sha256(Path(binary).read_bytes()).hexdigest()
        server, log = p.spawn([binary, '-L', 'tcp:127.0.0.1:5038', 'server', 'nodaemon'], 'adb-server.txt')
        probe.time.sleep(2)
        facts['server_exit'] = server.poll()
        facts['startup_diagnostic'] = classify_adb(log.read_text(errors='replace'))
        p.stop(server)
        # Current AOSP explicitly disables USB and mDNS with these flags.
        # Compare on the same disposable host; keep the loopback listener unchanged.
        p.env.update(ADB_USB='0', ADB_MDNS='0')
        p.env.pop('ADB_LIBUSB', None)
        server, log = p.spawn([binary, '-L', 'tcp:127.0.0.1:5038', 'server', 'nodaemon'], 'adb-isolated.txt')
        probe.time.sleep(2)
        facts['isolated_server_exit'] = server.poll()
        facts['isolated_startup_diagnostic'] = classify_adb(log.read_text(errors='replace'))
        p.stop(server)
        facts['status'] = 'diagnosis-collected'
    except Exception as e:
        facts.update(status='blocked', stage=p.stage, reason=str(e))
    finally:
        p.cleanup()
        facts['cleanup_errors'] = p.result.get('cleanup_errors', [])
        if facts['cleanup_errors']:
            facts['status'] = 'cleanup-failed'
        print(json.dumps(facts, sort_keys=True))
        for name in ('commands.jsonl', 'resources.jsonl'):
            f = evidence / name
            if f.is_file():
                print(name + '\n' + f.read_text()[-16000:])
    return 0 if facts['status'] == 'diagnosis-collected' else 1


if __name__ == '__main__':
    sys.exit(main())
