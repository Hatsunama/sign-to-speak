#!/usr/bin/env python3
"""Accept only the two deliberate cleanup-isolation assertions on both API levels."""
import pathlib
import sys
import xml.etree.ElementTree as ET

CLASS_NAME = 'com.xmilo_at_your_side.sign_to_speak.NativeBackPrivacyTest'
MESSAGES = {
    'cleanupFailureOnBackStillCancelsEveryViewAndDelegates': 'Back must isolate each camera cleanup failure before delegating',
    'cleanupFailureOnDestroyStillUnregistersEveryViewAndDelegates': 'Activity destruction must isolate each camera cleanup failure',
}

def require(condition, message):
    if not condition:
        raise AssertionError(message)

def verify(report, source):
    root = ET.parse(report).getroot()
    require(root.tag == 'testsuite' and root.attrib.get('name') == CLASS_NAME,
            'Wrong counterfactual test suite/class')
    tests = root.findall('testcase')
    require(len(tests) == 4, f'Expected 4 counterfactual cases, found {len(tests)}')
    expected = {name + suffix for name in MESSAGES for suffix in ('[28]', '')}
    require({t.attrib.get('name') for t in tests} == expected, 'Wrong counterfactual test names/API coverage')
    require(root.attrib.get('failures') == '4' and root.attrib.get('errors') == '0',
            'Counterfactual must contain only four assertion failures')
    lines = pathlib.Path(source).read_text().splitlines()
    for case in tests:
        require(case.attrib.get('classname') == CLASS_NAME, 'Wrong counterfactual test class')
        name = case.attrib['name'].removesuffix('[28]')
        message = MESSAGES[name]
        locations = [i for i, line in enumerate(lines, 1) if f'assertNull("{message}", escaped)' in line]
        require(len(locations) == 1, 'Expected assertion source location changed')
        failures = case.findall('failure')
        require(len(failures) == 1, 'Extra teardown or harness failures invalidate clean baseline evidence')
        failure = failures[0]
        require(case.find('skipped') is None and failure is not None, 'A counterfactual case did not fail')
        require(failure.attrib.get('type') == 'java.lang.AssertionError', 'Harness/setup failure is not behavioral red')
        exact = message + ' expected null, but was:<java.lang.UnsupportedOperationException: Injected CameraX cleanup failure>'
        require(failure.attrib.get('message', '').removeprefix('java.lang.AssertionError: ') == exact,
                f'Unexpected assertion: {failure.attrib.get("message")}')
        location = f'NativeBackPrivacyTest.{name}(NativeBackPrivacyTest.java:{locations[0]})'
        require(location in (failure.text or ''), 'Failure did not originate at the intended assertion')
    print('Confirmed four precise cleanup-isolation fail-before assertions (API28/36).')

if __name__ == '__main__':
    verify(sys.argv[1], sys.argv[2])
