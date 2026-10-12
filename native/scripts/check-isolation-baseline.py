#!/usr/bin/env python3
"""Accept only the two deliberate cleanup-isolation assertions on both API levels."""
import pathlib
import sys
import xml.etree.ElementTree as ET

MESSAGES = {
    'cleanupFailureOnBackStillCancelsEveryViewAndDelegates': 'Back must isolate each camera cleanup failure before delegating',
    'cleanupFailureOnDestroyStillUnregistersEveryViewAndDelegates': 'Activity destruction must isolate each camera cleanup failure',
}

def verify(report, source):
    root = ET.parse(report).getroot()
    tests = root.findall('testcase')
    assert len(tests) == 4, f'Expected 4 counterfactual cases, found {len(tests)}'
    expected = {name + suffix for name in MESSAGES for suffix in ('[28]', '')}
    assert {t.attrib['name'] for t in tests} == expected, 'Wrong counterfactual test names/API coverage'
    assert root.attrib.get('failures') == '4' and root.attrib.get('errors') == '0', 'Counterfactual must contain only four assertion failures'
    lines = pathlib.Path(source).read_text().splitlines()
    for case in tests:
        name = case.attrib['name'].removesuffix('[28]')
        message = MESSAGES[name]
        locations = [i for i, line in enumerate(lines, 1) if f'assertNull("{message}", escaped)' in line]
        assert len(locations) == 1, 'Expected assertion source location changed'
        failure = case.find('failure')
        assert case.find('skipped') is None and failure is not None, 'A counterfactual case did not fail'
        assert failure.attrib.get('type') == 'java.lang.AssertionError', 'Harness/setup failure is not behavioral red'
        exact = message + ' expected null, but was:<java.lang.UnsupportedOperationException: Injected CameraX cleanup failure>'
        assert failure.attrib.get('message') == exact, f'Unexpected assertion: {failure.attrib.get("message")}'
        location = f'NativeBackPrivacyTest.{name}(NativeBackPrivacyTest.java:{locations[0]})'
        assert location in (failure.text or ''), 'Failure did not originate at the intended assertion'
    print('Confirmed four precise cleanup-isolation fail-before assertions (API28/36).')

if __name__ == '__main__':
    verify(sys.argv[1], sys.argv[2])
