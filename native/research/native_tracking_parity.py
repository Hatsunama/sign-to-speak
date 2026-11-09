#!/usr/bin/env python3
"""Compile the production Kotlin SORT port and compare synthetic real-library output.

Explicit host-only test, not Android/device execution or recognition evidence.
Requires an already installed official Kotlin compiler and Java 17+; downloads nothing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import subprocess
import tempfile

import numpy as np

from hand_tracking import HolisticHandTracker, TrackingError

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION = ROOT / 'native/android/app/src/main/java/com/xmilo_at_your_side/sign_to_speak/RoboflowHandTracker.kt'
HARNESS = ROOT / 'native/tests/kotlin/RoboflowHandTrackerParity.kt'


def hand(x, y=.25):
    # Round to the exact production input type before either implementation sees it.
    return np.array([[x + (i % 5) * .012, y + (i // 5) * .015, i * .001]
                     for i in range(21)], dtype=np.float32)


def scenarios():
    """Deterministic scenarios avoid random-state or model/data dependencies."""
    rng = random.Random(20261109)
    yield 'reset'
    t = 0
    for i in range(180):
        # Two separated jittering, gradually translating hands.
        yield (t, 640, 480, hand(.1 + i * .0007 + rng.uniform(-.002, .002)),
               hand(.65 - i * .0005 + rng.uniform(-.002, .002), .4))
        t += rng.choice((16, 33, 50, 100))
    for crossing in range(8):
        yield 'reset'
        for i in range(80):
            # Cross without identical boxes (different y), then flip input slots.
            a, b = hand(.12 + i * .006, .25), hand(.74 - i * .006, .28)
            if crossing % 2 and i >= 40:
                a, b = b, a
            if i % 17 in (8, 9):
                a = None
            if 30 <= i < 38:
                b = None
            yield (i * 33, 640, 480, a, b)
    for gap in (250, 251, 500, 5000):
        yield 'reset'
        yield (0, 640, 480, hand(.2), hand(.7))
        yield (100, 640, 480, hand(.2), hand(.7))
        yield (100 + gap, 640, 480, hand(.2), hand(.7))
        yield (200 + gap, 640, 480, hand(.2), hand(.7))
    yield 'reset'
    for i in range(40):
        # Expire one identity while continuing to submit empty measurements.
        a = hand(.2) if i < 4 or i >= 30 else None
        yield (i * 100, 640, 480, a, None)
    yield 'reset'
    yield (0, 1280, 720, hand(.1), hand(.6))
    yield (100, 1280, 720, hand(.1), hand(.6))
    # Bad inputs must not mutate state; valid retry at the same timestamp succeeds.
    yield (200, 640, 480, hand(.1), hand(.6))
    yield (100, 1280, 720, hand(.1), hand(.6))
    malformed = hand(.1); malformed[0, 0] = np.nan
    yield (200, 1280, 720, malformed, hand(.6))
    yield (200, 1280, 720, hand(.1), hand(.6))
    yield 'reset'
    yield (0, 640, 480, np.full((21, 3), np.nan, dtype=np.float32), None)
    yield (100, 640, 480, hand(.3), None)
    yield (200, 640, 480, hand(.3), None)
    # Exact binary-representable boxes: width13, shift7 => IoU6/20=.3.
    def threshold_box(shift):
        points = np.zeros((21, 3), dtype=np.float32)
        for i in range(21):
            points[i, :2] = [.25 + shift + (13 / 1024 if i % 2 else 0),
                             .25 + (13 / 1024 if i % 3 else 0)]
        return points
    boundary = np.float32(.25 + 7 / 1024)
    for position in (np.nextafter(boundary, np.float32(0)), boundary,
                     np.nextafter(boundary, np.float32(1))):
        yield 'reset'
        yield (0, 1024, 1024, threshold_box(0), None)
        yield (100, 1024, 1024, threshold_box(float(position) - .25), None)
    for elapsed in (500, 501):
        yield 'reset'
        yield (0, 640, 480, hand(.2), None)
        yield (100, 640, 480, hand(.2), None)
        yield (300, 640, 480, None, None)
        yield (500, 640, 480, None, None)
        yield (100 + elapsed, 640, 480, hand(.2), None)
    # Equal-cost assignment is deliberately included, without claiming all ties
    # must match SciPy across platforms or dependency versions.
    yield 'reset'
    for i in range(5):
        yield (i * 100, 640, 480, hand(.2), hand(.2))
    yield 'close'
    yield (500, 640, 480, hand(.3), None)
    yield 'reset'


def prepare():
    tracker = HolisticHandTracker()
    closed = False
    commands, expected = [], []
    for item in scenarios():
        if isinstance(item, str):
            commands.append(item)
            if closed:
                expected.append('error')
            elif item == 'close':
                closed = True
                expected.append('close')
            else:
                tracker.reset()
                expected.append('reset')
            continue
        timestamp, width, height, left, right = item
        def encode(points):
            return 'null' if points is None else ','.join('NaN' if np.isnan(v) else str(float(v)) for v in points.ravel())
        commands.append(f'{timestamp}|{width}|{height}|{encode(left)}|{encode(right)}')
        if closed:
            expected.append('error')
            continue
        features = np.full((543, 3), np.nan, dtype=np.float64)
        if left is not None:
            features[468:489] = left
        if right is not None:
            features[522:543] = right
        try:
            result = tracker.update(features, timestamp_ms=timestamp, width=width, height=height)
            expected.append(';'.join(f'{r.side},{r.generation},{r.tracker_id if r.tracker_id is not None else "null"},{str(r.continuous).lower()}'
                                     for r in result) or 'empty')
        except TrackingError:
            expected.append('error')
    return commands, expected


def run(kotlinc, java):
    commands, expected = prepare()
    with tempfile.TemporaryDirectory(prefix='sign-kotlin-parity-') as tmp:
        jar = Path(tmp) / 'parity.jar'
        subprocess.run([str(kotlinc), '-J-Xmx512m', str(PRODUCTION), str(HARNESS),
                        '-include-runtime', '-d', str(jar)], check=True, timeout=120)
        contract = subprocess.run([str(java), '-Xmx128m', '-jar', str(jar), '--contract'],
                                  text=True, capture_output=True, check=True, timeout=60)
        if contract.stdout.strip() != 'Native contract checks passed':
            raise AssertionError('Native contract harness did not report completion')
        completed = subprocess.run([str(java), '-Xmx128m', '-jar', str(jar)],
                                   input='\n'.join(commands) + '\n', text=True,
                                   capture_output=True, check=True, timeout=60)
    actual = completed.stdout.splitlines()
    if len(actual) != len(expected):
        raise AssertionError(f'Native emitted {len(actual)} results; expected {len(expected)}')
    failures = [(i, want, got) for i, (want, got) in enumerate(zip(expected, actual, strict=True)) if want != got]
    if failures:
        # Never include input coordinates; these are generated fixtures, still keep diagnostics bounded.
        raise AssertionError(f'{len(failures)} parity mismatches; first five (index, Python, Kotlin): {failures[:5]}')
    return {'scope': 'Synthetic JVM parity only; not Android execution or ASL accuracy',
            'protocol_steps': len(commands), 'measured_frames': sum('|' in c for c in commands),
            'matched_steps': len(actual), 'native_contract_checks': 'passed', 'seed': 20261109}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kotlinc', type=Path, required=True)
    parser.add_argument('--java', default='java')
    args = parser.parse_args()
    print(json.dumps(run(args.kotlinc, args.java), indent=2))


if __name__ == '__main__':
    main()
