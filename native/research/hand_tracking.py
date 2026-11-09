#!/usr/bin/env python3
"""Offline, host-only Roboflow SORT association for the 543-point research contract.

No camera, model, classifier, speech, service, upload or native-app integration.
This associates measured hand boxes; it never generates/relabels landmarks.
Run with --help for the explicit local fixture CLI. See ROBOFLOW_TRACKING.md.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import sys
import tempfile

import numpy as np
import supervision as sv
from trackers import SORTTracker

TRACKERS_VERSION = '2.7.0'
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_FRAMES = 1800
MAX_TIMESTAMP_MS = 24 * 60 * 60 * 1000
HAND_SLICES = (('left', slice(468, 489)), ('right', slice(522, 543)))


class TrackingError(ValueError):
    """Invalid research input or a failed tracker; no raw input in error text."""


@dataclass(frozen=True)
class HandTrack:
    side: str
    generation: int
    tracker_id: int | None
    # True only for consecutive measured frames with the same confirmed ID/slot.
    # Not a recognition confidence, validity judgment or proof of identity.
    continuous: bool


def _number(value, name, minimum, maximum):
    if (type(value) not in (int, float) or not minimum <= value <= maximum
            or not math.isfinite(value)):
        raise TrackingError(f'{name} must be a finite number within the documented bounds')


def _dimensions(width, height):
    if any(type(value) is not int or not 1 <= value <= 16384 for value in (width, height)):
        raise TrackingError('width/height must be integer pixel dimensions in 1..16384')


class HolisticHandTracker:
    """One instance per clip and fixed, unmirrored decoded-image coordinate space.

    Features use the existing research layout (543, 3), with all-NaN absent hands.
    Handedness comes from the detector, never the track ID. SORT is class-agnostic
    and may switch IDs at crossings: outputs must not reorder model features.
    A gap over 250 ms resets association; a missing hand breaks continuity even
    if its geometric ID is recovered within SORT's 500 ms lost-track budget.
    """

    def __init__(self):
        if version('trackers') != TRACKERS_VERSION:
            raise TrackingError('Install the pinned host research dependency lock')
        self.reset()

    def reset(self):
        self._generation = getattr(self, '_generation', -1) + 1
        self._tracker = SORTTracker(
            lost_track_buffer=15, frame_rate=30.0,
            minimum_consecutive_frames=2, minimum_iou_threshold=0.3,
        )
        self._timestamp_ms = None
        self._dimensions = None
        self._previous: dict[str, int] = {}

    def update(self, features, *, timestamp_ms, width, height) -> tuple[HandTrack, ...]:
        # Validate the complete frame before advancing any state.
        _number(timestamp_ms, 'timestamp_ms', 0, MAX_TIMESTAMP_MS)
        _dimensions(width, height)
        if self._timestamp_ms is not None and timestamp_ms <= self._timestamp_ms:
            raise TrackingError('timestamp_ms must strictly increase within a clip')
        if self._dimensions is not None and self._dimensions != (width, height):
            raise TrackingError('Image resolution changed; reset before a new coordinate space')
        try:
            data = np.asarray(features)
        except (ValueError, TypeError) as exc:
            raise TrackingError('Features must be a numeric (543, 3) array') from exc
        if data.shape != (543, 3) or data.dtype.kind not in 'fiu':
            raise TrackingError('Features must be a numeric (543, 3) array')
        boxes, sides = [], []
        for side, indexes in HAND_SLICES:
            points = data[indexes]
            if np.isnan(points).all():
                continue
            if not np.isfinite(points).all():
                raise TrackingError('A hand must be fully finite or entirely NaN when absent')
            # Permit modest off-image landmarks; do not clip or warp the model's
            # features. Bound coordinates before float32/Kalman conversion.
            if np.any(points < -4) or np.any(points > 4):
                raise TrackingError('Normalized hand coordinates must lie within -4..4')
            xy = points[:, :2] * np.array([width, height])
            low, high = xy.min(axis=0), xy.max(axis=0)
            if np.any(high - low < 1):
                raise TrackingError('Hand box must span at least one pixel on each axis')
            boxes.append([low[0], low[1], high[0], high[1]])
            sides.append(side)
        detections = sv.Detections(
            xyxy=np.asarray(boxes, dtype=np.float32).reshape(-1, 4),
            # Holistic supplies no calibrated box detection confidence. Use SORT
            # with confidence=None, rather than inventing ByteTrack scores.
            data={'input_row': np.arange(len(sides), dtype=np.int64)},
        )
        if self._timestamp_ms is not None and timestamp_ms - self._timestamp_ms > 250:
            self.reset()
        try:
            tracked = self._tracker.update(detections, timestamp=timestamp_ms / 1000.0)
            identifiers = [None] * len(sides)
            rows = tracked.data.get('input_row')
            if rows is None or tracked.tracker_id is None or len(rows) != len(sides):
                raise TrackingError('Tracker did not preserve the measured detection rows')
            seen = set()
            for row, identifier in zip(rows, tracked.tracker_id, strict=True):
                if any(not isinstance(value, (int, np.integer)) or isinstance(value, (bool, np.bool_))
                       for value in (row, identifier)):
                    raise TrackingError('Tracker returned noninteger detection identities')
                row, identifier = int(row), int(identifier)
                if row in seen or not 0 <= row < len(sides) or identifier < -1:
                    raise TrackingError('Tracker returned an invalid detection mapping')
                seen.add(row)
                identifiers[row] = identifier if identifier >= 0 else None
            confirmed = [value for value in identifiers if value is not None]
            if len(set(confirmed)) != len(confirmed):
                raise TrackingError('Tracker assigned duplicate hand identities')
        except Exception as exc:
            # Do not allow recovery from a partially advanced third-party state.
            self.reset()
            raise TrackingError('Tracking failed; state reset, restart the clip') from exc
        result = tuple(HandTrack(side, self._generation, identifier,
                                identifier is not None and self._previous.get(side) == identifier)
                       for side, identifier in zip(sides, identifiers, strict=True))
        self._previous = {item.side: item.tracker_id for item in result if item.tracker_id is not None}
        self._timestamp_ms = timestamp_ms
        self._dimensions = (width, height)
        return result


def _keys(value, expected, name):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise TrackingError(f'{name} has missing or unexpected fields')


def track_document(document):
    """Process bounded local fixture data; return IDs/status only, never landmarks."""
    _keys(document, ('schema_version', 'width', 'height', 'frames'), 'document')
    if type(document['schema_version']) is not int or document['schema_version'] != 1:
        raise TrackingError('Unsupported schema_version')
    width, height = document['width'], document['height']
    _dimensions(width, height)
    frames = document['frames']
    if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_FRAMES:
        raise TrackingError('frames must contain 1..1800 measured frames')
    tracker = HolisticHandTracker()
    output = []
    for frame in frames:
        _keys(frame, ('timestamp_ms', 'left', 'right'), 'frame')
        features = np.full((543, 3), np.nan, dtype=np.float64)
        for side, indexes in HAND_SLICES:
            points = frame[side]
            if points is None:
                continue
            if not isinstance(points, list) or len(points) != 21:
                raise TrackingError('Present hand must contain exactly 21 xyz points')
            for point in points:
                if not isinstance(point, list) or len(point) != 3:
                    raise TrackingError('Hand point must contain exactly three coordinates')
                for coordinate in point:
                    _number(coordinate, 'coordinate', -4, 4)
            features[indexes] = points
        tracks = tracker.update(features, timestamp_ms=frame['timestamp_ms'], width=width, height=height)
        output.append({'timestamp_ms': frame['timestamp_ms'], 'hands': [asdict(item) for item in tracks]})
    return {
        'schema_version': 1,
        'scope': 'Offline host association diagnostics only; not Android tracking or ASL accuracy',
        'tracker': {'package': 'trackers', 'version': version('trackers'), 'algorithm': 'SORTTracker',
                    'supervision_version': version('supervision'), 'numpy_version': version('numpy'),
                    'scipy_version': version('scipy'),
                    'lost_track_buffer': 15, 'frame_rate': 30.0, 'minimum_consecutive_frames': 2,
                    'minimum_iou_threshold': 0.3, 'max_gap_ms': 250, 'confidence': None},
        'adapter_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'frames': output,
    }


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TrackingError('Duplicate JSON field')
        result[key] = value
    return result


def _constant(_value):
    raise TrackingError('Nonstandard JSON number; encode an absent hand as null')


def run_file(source, target):
    source, target = Path(source), Path(target)
    if target.exists() or target.is_symlink():
        raise TrackingError('Output must be a fresh file')
    try:
        if not source.is_file() or source.is_symlink():
            raise TrackingError('Input must be a regular non-symlink file')
        with source.open('rb') as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
        if not 1 <= len(raw) <= MAX_INPUT_BYTES:
            raise TrackingError('Input must be 1 byte to 4 MiB')
        document = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
        result = track_document(document)
        result['input_sha256'] = hashlib.sha256(raw).hexdigest()
        encoded = json.dumps(result, indent=2, allow_nan=False) + '\n'
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, TrackingError):
            raise
        raise TrackingError('Could not read a valid bounded JSON input') from exc
    temporary = None
    try:
        # Publish only a complete file. A hard link is atomic and refuses to
        # overwrite any concurrent file/symlink, unlike replace/rename.
        descriptor, temporary = tempfile.mkstemp(prefix='.sign-tracks-', dir=target.parent)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
    except OSError as exc:
        raise TrackingError('Could not create the fresh output file') from exc
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='Explicit local JSON hand fixture (max 4 MiB)')
    parser.add_argument('output', type=Path, help='Fresh local diagnostic JSON; never uploaded')
    args = parser.parse_args()
    try:
        run_file(args.input, args.output)
    except TrackingError as exc:
        print(f'Tracking failed: {exc}', file=sys.stderr)
        return 2
    print('Wrote local association diagnostics. No recognition accuracy claim.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
