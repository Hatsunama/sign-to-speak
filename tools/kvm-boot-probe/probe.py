#!/usr/bin/env python3
"""One disposable environment probe. No app builds, installs, or acceptance claims."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse

BRANCH = 'refs/heads/probe/sign-kvm-matrix-20261013'
SOURCE_BASE = '78c71bd36bbd0f04c86bd85e4b795da35bcd56f7'
PROBE_PARENT = '37d0be76ba4f7fa30ab3c913261375d8d866b853'
CELLS = {
    'api26': ('26', 'google_apis', '16', '4096'),
    'api35': ('35', 'google_apis', '9', '4096'),
    'api36': ('36', 'google_apis', '7', '4096'),
    'api35-16k': ('35', 'google_apis_ps16k', '5', '16384'),
}
PACKAGES = {}
IMAGE = ''
EXPECTED_SDK = ''
EXPECTED_PAGE = ''
TOTAL_SECONDS = 20 * 60
BOOT_SECONDS = 10 * 60
LOG_LIMIT = 2 * 1024 * 1024


def select_cell(name):
    global PACKAGES, IMAGE, EXPECTED_SDK, EXPECTED_PAGE
    if name not in CELLS:
        raise Blocked('UNKNOWN_CELL')
    sdk, tag, revision, page = CELLS[name]
    IMAGE = f'system-images;android-{sdk};{tag};x86_64'
    PACKAGES = {'emulator': '37.2.12', 'platform-tools': '37.0.1', IMAGE: revision}
    EXPECTED_SDK, EXPECTED_PAGE = sdk, page


class Blocked(RuntimeError):
    pass


def policy(env):
    checks = [('public-repository', env.get('PROBE_REPOSITORY_PRIVATE') == 'false'
               and env.get('PROBE_REPOSITORY_VISIBILITY') == 'public'),
              ('github-hosted-runner', env.get('GITHUB_ACTIONS') == 'true'
               and env.get('RUNNER_ENVIRONMENT') == 'github-hosted'),
              ('repository-identity', env.get('GITHUB_REPOSITORY') == 'Hatsunama/sign-to-speak'),
              ('exact-trigger', env.get('GITHUB_EVENT_NAME') == 'push'
               and env.get('GITHUB_REF') == BRANCH),
              ('ubuntu-24.04-image', env.get('ImageOS') == 'ubuntu24'
               and env.get('RUNNER_OS') == 'Linux'
               and env.get('RUNNER_ARCH') == 'X64'),
              ('commit-identity', bool(re.fullmatch(r'[0-9a-f]{40}', env.get('PROBE_COMMIT', ''))))]
    for name, ok in checks:
        if not ok:
            raise Blocked('POLICY_BLOCKER:' + name)


def verify_ubuntu_package_uris(text):
    urls = re.findall(r"^'([^']+)'", text, re.M)
    if not urls:
        raise Blocked('APT_DOWNLOAD_PLAN_MISSING')
    for url in urls:
        parsed = urlparse(url)
        if (parsed.scheme not in ('http', 'https') or parsed.hostname not in
                ('archive.ubuntu.com', 'security.ubuntu.com', 'azure.archive.ubuntu.com') or
                not parsed.path.startswith('/ubuntu/pool/') or not parsed.path.endswith('.deb') or
                parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise Blocked('NON_OFFICIAL_UBUNTU_PACKAGE_SOURCE')
    return len(urls)


class KvmLease:
    """Only this runner UID, only the known disposable /dev/kvm device."""
    def __init__(self):
        self.changed = False
        self.uid = os.getuid()
        self.before = None

    def command(self, args):
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=15, check=True)
        return result.stdout.strip()

    def acquire(self):
        p = Path('/dev/kvm')
        info = p.lstat()
        if (not stat.S_ISCHR(info.st_mode) or info.st_uid != 0 or
                stat.S_IMODE(info.st_mode) != 0o660 or self.uid == 0 or
                (os.major(info.st_rdev), os.minor(info.st_rdev)) != (10, 232)):
            raise Blocked('KVM_DEVICE_IDENTITY_OR_MODE')
        self.before = self.command(['getfacl', '-cp', '/dev/kvm'])
        if self.before != 'user::rw-\ngroup::rw-\nother::---':
            raise Blocked('KVM_UNEXPECTED_EXISTING_ACL')
        if not os.access(p, os.R_OK | os.W_OK):
            # Mark before the call so cleanup also handles an uncertain interrupted grant.
            self.changed = True
            self.command(['sudo', '-n', 'setfacl', '-m', f'u:{self.uid}:rw', '/dev/kvm'])
        if not os.access(p, os.R_OK | os.W_OK):
            raise Blocked('KVM_ACCESS_NOT_GRANTED')
        return {'uid': self.uid, 'mode': '0660', 'device': '10:232',
                'runner_only_acl_added': self.changed, 'read_write': True}

    def release(self):
        if self.changed:
            self.command(['sudo', '-n', 'setfacl', '--set',
                          'u::rw-,g::rw-,o::---', '/dev/kvm'])
            if self.command(['getfacl', '-cp', '/dev/kvm']) != self.before:
                raise Blocked('KVM_ACL_RESTORE_FAILED')
            self.changed = False


def parse_available(text):
    found = {}
    in_available = False
    for line in text.splitlines():
        if line.strip() == 'Available Packages:':
            in_available = True
            continue
        if line.strip().endswith(':'):
            in_available = False
        if in_available and '|' in line:
            fields = [s.strip() for s in line.split('|')]
            if fields[0] in PACKAGES:
                if fields[0] in found:
                    raise Blocked('AMBIGUOUS_PACKAGE:' + fields[0])
                found[fields[0]] = fields[1]
    if found != PACKAGES:
        raise Blocked('PACKAGE_REVISION_DRIFT:' + json.dumps(found, sort_keys=True))
    return found


def properties(path):
    return dict(line.strip().split('=', 1) for line in path.read_text().splitlines()
                if '=' in line and not line.lstrip().startswith('#'))


def validate_runtime(facts):
    required = {'boot_completed': '1', 'page_size': EXPECTED_PAGE, 'sdk': EXPECTED_SDK, 'abi': 'x86_64'}
    for k, v in required.items():
        if facts.get(k) != v:
            raise Blocked('RUNTIME_MISMATCH:' + k)
    if not facts.get('fingerprint') or 'x86_64' not in facts.get('abilist', '').split(','):
        raise Blocked('RUNTIME_FACTS_INCOMPLETE')


class Probe:
    def __init__(self, evidence, temp_root):
        self.evidence = evidence
        self.temp_root = temp_root
        self.deadline = time.monotonic() + TOTAL_SECONDS
        self.children = []
        self.files = []
        self.stage = 'preflight'
        self.work = None
        self.env = None
        self.kvm = KvmLease()
        self.result = {'status': 'blocked', 'scope': 'environment-only', 'page_size_verified': False}

    def write(self, name, value):
        (self.evidence / name).write_text(value)

    def remaining(self, requested):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise Blocked('TOTAL_DEADLINE_EXCEEDED')
        return min(requested, left)

    def spawn(self, args, name):
        log = self.evidence / name
        f = log.open('wb')
        self.files.append(f)
        p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT,
                             env=self.env, start_new_session=True)
        self.children.append(p)
        return p, log

    def stop(self, p):
        # Signal only process groups created and owned by this probe; no global pkill.
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            raise Blocked('OWNED_PROCESS_CLEANUP_FAILED')
        if p in self.children:
            self.children.remove(p)

    def run(self, args, name, seconds=30, allow_failure=False):
        end = time.monotonic() + self.remaining(seconds)
        p, log = self.spawn(args, name)
        while p.poll() is None:
            now = time.monotonic()
            if log.stat().st_size > LOG_LIMIT:
                self.stop(p)
                raise Blocked('COMMAND_LOG_LIMIT:' + name)
            if now >= self.deadline:
                self.stop(p)
                raise Blocked('TOTAL_DEADLINE_EXCEEDED')
            if now >= end:
                self.stop(p)
                if allow_failure:
                    with (self.evidence / 'commands.jsonl').open('a') as f:
                        f.write(json.dumps({'stage': self.stage, 'log': name,
                                            'result': 'transient-command-timeout'}) + '\n')
                    return ''
                raise Blocked('COMMAND_TIMEOUT:' + name)
            time.sleep(.2)
        code = p.returncode
        with (self.evidence / 'commands.jsonl').open('a') as f:
            f.write(json.dumps({'stage': self.stage, 'log': name, 'exit': code}) + '\n')
        self.stop(p)  # Also reap any child left in its owned process group.
        text = log.read_text(errors='replace')[:LOG_LIMIT]
        if code and not allow_failure:
            if re.search(r'license.*(not accepted|accept|review)|Accept\?.*y/N', text, re.I):
                raise Blocked('LICENSE_BLOCKER:' + name + ': no acceptance was supplied')
            missing = re.search(r'error while loading shared libraries:\s*([A-Za-z0-9_+.-]+\.so(?:\.[A-Za-z0-9_.-]+)*):', text)
            if missing:
                raise Blocked('MISSING_HOST_LIBRARY:' + missing[1])
            raise Blocked('COMMAND_FAILED:' + name + ':exit=' + str(code))
        return '' if code else text.strip()

    def resources(self, minimum_disk_gib, minimum_mem_gib):
        free = shutil.disk_usage(self.temp_root).free
        mem = int(re.search(r'^MemAvailable:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M)[1]) * 1024
        cpus = os.cpu_count() or 0
        with (self.evidence / 'resources.jsonl').open('a') as f:
            f.write(json.dumps({'stage': self.stage, 'free_disk_bytes': free,
                                'available_memory_bytes': mem, 'logical_cpus': cpus}) + '\n')
        if free < minimum_disk_gib * 2**30 or mem < minimum_mem_gib * 2**30 or cpus < 2:
            raise Blocked('RESOURCE_BLOCKER:' + self.stage)

    def setup(self):
        original = Path(os.environ.get('ANDROID_SDK_ROOT') or os.environ.get('ANDROID_HOME', ''))
        cmd = original / 'cmdline-tools/latest'
        if not (cmd / 'bin/sdkmanager').is_file() or not (cmd / 'bin/avdmanager').is_file():
            raise Blocked('PREINSTALLED_COMMAND_LINE_TOOLS_MISSING')
        licenses = original / 'licenses'
        accepted = sorted(p for p in licenses.glob('*') if p.is_file() and not p.is_symlink())
        if not accepted:
            raise Blocked('LICENSE_BLOCKER:no preaccepted runner license markers')
        self.write('runner-license-marker-hashes.json', json.dumps({
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in accepted}, indent=2))
        inventory = {}
        for name in ['cmdline-tools;latest', *PACKAGES]:
            p = original.joinpath(*name.split(';')) / 'source.properties'
            inventory[name] = properties(p).get('Pkg.Revision') if p.is_file() else 'not-installed'
        self.write('preinstalled-revisions.json', json.dumps(inventory, indent=2))
        self.resources(12, 6)
        # Before any tool execution, package download, adb server or emulator launch.
        self.write('kvm-access.json', json.dumps(self.kvm.acquire(), indent=2))
        self.work = Path(tempfile.mkdtemp(prefix='sign-kvm-', dir=self.temp_root))
        sdk = self.work / 'sdk'
        home = self.work / 'home'
        android = home / '.android'
        for p in (sdk / 'licenses', android / 'avd', self.work / 'tmp'):
            p.mkdir(parents=True, exist_ok=True)
        shutil.copytree(cmd, sdk / 'cmdline-tools/latest')
        for p in accepted:
            shutil.copy2(p, sdk / 'licenses' / p.name)
        # Allowlist child environment: never inherit access tokens, signing data,
        # existing user home/ADB key paths, Android custom repositories or proxies.
        self.env = {k: os.environ[k] for k in ('PATH', 'JAVA_HOME', 'LANG') if k in os.environ}
        self.env.update(HOME=str(home), ANDROID_USER_HOME=str(android),
                        ANDROID_EMULATOR_HOME=str(android), ANDROID_AVD_HOME=str(android / 'avd'),
                        ANDROID_HOME=str(sdk), ANDROID_SDK_ROOT=str(sdk),
                        TMPDIR=str(self.work / 'tmp'),
                        ADB_SERVER_SOCKET='tcp:127.0.0.1:5038', ANDROID_ADB_SERVER_PORT='5038',
                        ADB_MDNS_AUTO_CONNECT='0', ADB_LIBUSB='0')
        self.sdk = sdk
        self.sdkmanager = str(sdk / 'cmdline-tools/latest/bin/sdkmanager')
        self.avdmanager = str(sdk / 'cmdline-tools/latest/bin/avdmanager')
        self.write('command-line-tools-properties.txt', (cmd / 'source.properties').read_text())

    def execute(self):
        self.setup()
        self.stage = 'package-metadata'
        listing = self.run([self.sdkmanager, '--list', '--channel=0', '--sdk_root=' + str(self.sdk)],
                           'available-packages.txt', seconds=90)
        self.write('expected-packages.json', json.dumps(parse_available(listing), indent=2))
        self.stage = 'package-installation'
        self.run([self.sdkmanager, '--channel=0', '--sdk_root=' + str(self.sdk), *PACKAGES],
                 'sdk-install.txt', seconds=360)
        # Detect missing packages and metadata races rather than silently accepting newer revisions.
        installed = {}
        for name, revision in PACKAGES.items():
            path = self.sdk.joinpath(*name.split(';')) / 'source.properties'
            if not path.is_file():
                raise Blocked('PACKAGE_NOT_INSTALLED_OR_LICENSE_BLOCKER:' + name)
            props = properties(path)
            installed[name] = props.get('Pkg.Revision')
            if installed[name] != revision:
                raise Blocked('INSTALLED_REVISION_MISMATCH:' + name)
        self.write('installed-packages.json', json.dumps(installed, indent=2))
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in (self.sdk / 'licenses').glob('*') if p.is_file()}
        before = json.loads((self.evidence / 'runner-license-marker-hashes.json').read_text())
        if after != before:
            raise Blocked('LICENSE_MARKERS_CHANGED')
        self.stage = 'cli-verification'
        emulator = str(self.sdk / 'emulator/emulator')
        adb = str(self.sdk / 'platform-tools/adb')
        self.run([emulator, '-version'], 'emulator-version.txt')
        self.run([adb, 'version'], 'adb-version.txt')
        accel = self.run([emulator, '-help-accel'], 'emulator-help-accel.txt')
        gpu = self.run([emulator, '-help-gpu'], 'emulator-help-gpu.txt')
        helptext = self.run([emulator, '-help-all'], 'emulator-help-all.txt')
        if not re.search(r'\bon\b', accel) or not re.search(r'\bsoftware\b', gpu):
            raise Blocked('CLI_MODES_NOT_ADVERTISED')
        for option in ('no-window', 'no-audio', 'no-snapshot', 'camera-back', 'camera-front', 'port'):
            if option not in helptext:
                raise Blocked('CLI_OPTION_NOT_ADVERTISED:' + option)
        self.run([emulator, '-accel-check'], 'accel-check.txt')
        self.result['acceleration_check_passed'] = True
        self.stage = 'avd-creation'
        self.run([self.avdmanager, 'create', 'avd', '--name', 'sign_kvm_probe', '--package', IMAGE,
                  '--device', 'pixel_2', '--path', str(self.work / 'avd')], 'avd-create.txt', seconds=30)
        config = self.work / 'avd/config.ini'
        with config.open('a') as f:
            f.write('\nhw.ramSize=2048\nhw.cpu.ncore=2\nhw.lcd.width=480\nhw.lcd.height=800\n'
                    'hw.lcd.density=160\ndisk.dataPartition.size=2G\nhw.sdCard=no\n'
                    'hw.camera.back=none\nhw.camera.front=none\nhw.audioInput=no\n')
        self.write('avd-config.txt', config.read_text())
        self.stage = 'prelaunch-resources'
        self.resources(4, 5)
        self.stage = 'adb-server'
        # Foreground server on a probe-only port so cleanup owns its process group.
        server, serverlog = self.spawn([adb, '-L', 'tcp:127.0.0.1:5038', 'server', 'nodaemon'], 'adb-server.txt')
        time.sleep(1)
        if server.poll() is not None:
            raise Blocked('ADB_SERVER_EXITED')
        self.stage = 'emulator-boot'
        args = [emulator, '-avd', 'sign_kvm_probe', '-accel', 'on', '-gpu', 'software',
                '-no-window', '-no-audio', '-no-snapshot', '-no-boot-anim',
                '-camera-back', 'none', '-camera-front', 'none', '-port', '5556',
                '-memory', '2048', '-cores', '2']
        self.write('emulator-argv.json', json.dumps(args, indent=2))
        emu, emulog = self.spawn(args, 'emulator.txt')
        end = time.monotonic() + self.remaining(BOOT_SECONDS)
        def shell(command, filename):
            return self.run([adb, '-P', '5038', '-s', 'emulator-5556', 'shell', *command],
                            filename, seconds=8, allow_failure=True).replace('\r', '').strip()
        while time.monotonic() < end:
            if emu.poll() is not None:
                raise Blocked('EMULATOR_EXITED:exit=' + str(emu.returncode))
            if server.poll() is not None:
                raise Blocked('ADB_SERVER_EXITED_DURING_BOOT')
            if emulog.stat().st_size > LOG_LIMIT or serverlog.stat().st_size > LOG_LIMIT:
                raise Blocked('EMULATOR_OR_ADB_LOG_LIMIT')
            self.resources(2, 1)
            boot = shell(['getprop', 'sys.boot_completed'], 'boot-last.txt')
            if boot == '1':
                self.stage = 'runtime-facts'
                facts = {'boot_completed': boot,
                         'page_size': shell(['getconf', 'PAGE_SIZE'], 'page-size.txt')}
                for key, prop in {'sdk': 'ro.build.version.sdk', 'abi': 'ro.product.cpu.abi',
                                  'abilist': 'ro.product.cpu.abilist',
                                  'fingerprint': 'ro.build.fingerprint'}.items():
                    facts[key] = shell(['getprop', prop], key + '.txt')
                self.write('runtime-facts.json', json.dumps(facts, indent=2))
                validate_runtime(facts)
                self.result.update(status='environment-boot-verified', page_size_verified=True)
                return
            time.sleep(min(10, max(0, end - time.monotonic())))
        raise Blocked('BOOT_TIMEOUT: no verified Android boot within bounded exploratory window')

    def cleanup(self):
        errors = []
        for p in list(reversed(self.children)):
            try:
                self.stop(p)
            except Exception as e:
                errors.append(str(e))
        try:
            self.kvm.release()
        except Exception as e:
            errors.append('KVM cleanup: ' + str(e))
        for f in self.files:
            f.close()
        # Persist only bounded allowlisted files from this newly-created evidence dir.
        for p in self.evidence.iterdir():
            if p.is_file() and p.stat().st_size > LOG_LIMIT:
                with p.open('r+b') as f:
                    f.truncate(LOG_LIMIT)
                self.result.setdefault('truncated_logs', []).append(p.name)
        if self.work:
            shutil.rmtree(self.work)
        self.result['cleanup_errors'] = errors
        if errors:
            self.result['status'] = 'cleanup-failed'
        return not errors


def main():
    # Policy failure cannot execute an SDK binary, start adb or change /dev/kvm.
    policy(os.environ)
    select_cell(os.environ.get('PROBE_CELL', ''))
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    if actual != os.environ['PROBE_COMMIT']:
        raise Blocked('CHECKOUT_SHA_MISMATCH')
    parent = subprocess.check_output(['git', 'rev-parse', 'HEAD^'], text=True).strip()
    if parent != PROBE_PARENT:
        raise Blocked('SOURCE_BASE_MISMATCH')
    temp_root = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    evidence = temp_root / 'sign-kvm-boot-evidence'
    evidence.mkdir(exist_ok=False)
    probe = Probe(evidence, temp_root)
    probe.write('run.json', json.dumps({k: os.environ.get(k) for k in
                ('PROBE_COMMIT', 'PROBE_CELL', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'ImageOS', 'ImageVersion')}, indent=2))
    def interrupted(signum, frame):
        raise Blocked('INTERRUPTED:' + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        probe.execute()
    except Exception as e:
        probe.result['boundary'] = probe.stage
        probe.result['reason'] = str(e)
    finally:
        try:
            probe.cleanup()
        except Exception as e:
            probe.result['status'] = 'cleanup-failed'
            probe.result['cleanup_errors'] = [str(e)]
        probe.write('result.json', json.dumps(probe.result, indent=2) + '\n')
        print(json.dumps(probe.result))
        for name in ('run.json', 'resources.jsonl', 'preinstalled-revisions.json',
                     'runner-license-marker-hashes.json', 'installed-packages.json',
                     'commands.jsonl', 'kvm-access.json', 'runtime-facts.json'):
            p = evidence / name
            if p.is_file():
                print('\n--- evidence: ' + name + ' (last 32768 bytes maximum) ---')
                with p.open('rb') as f:
                    f.seek(max(0, p.stat().st_size - 32768))
                    print(f.read(32768).decode(errors='replace'))
    return 0 if probe.result['status'] == 'environment-boot-verified' else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Blocked as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
