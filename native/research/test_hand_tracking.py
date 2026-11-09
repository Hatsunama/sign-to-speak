"""Synthetic tests execute the real pinned Roboflow SORT implementation."""
import copy
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from hand_tracking import HolisticHandTracker, TrackingError, track_document, run_file


def hand(x, y=.25):
    return [[x + (i % 5) * .012, y + (i // 5) * .015, i * .001] for i in range(21)]


def features(left=None, right=None):
    result = np.full((543, 3), np.nan)
    if left is not None:
        result[468:489] = left
    if right is not None:
        result[522:543] = right
    return result


def frame(t, left=None, right=None):
    return {'timestamp_ms': t, 'left': left, 'right': right}


def document(frames):
    return {'schema_version': 1, 'width': 640, 'height': 480, 'frames': frames}


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = HolisticHandTracker()

    def update(self, t, left=None, right=None, **kwargs):
        return self.tracker.update(features(left, right), timestamp_ms=t,
                                   width=kwargs.get('width', 640), height=480)

    def test_real_library_confirmation_and_motion(self):
        self.assertEqual(type(self.tracker._tracker).__module__, 'trackers.core.sort.tracker')
        self.assertIsNone(self.update(0, hand(.2))[0].tracker_id)
        second = self.update(100, hand(.205))[0]
        third = self.update(200, hand(.21))[0]
        self.assertIsNotNone(second.tracker_id)
        self.assertFalse(second.continuous)
        self.assertEqual(second.tracker_id, third.tracker_id)
        self.assertTrue(third.continuous)

    def test_two_hands_keep_anatomical_slots(self):
        for t in (0, 100, 200):
            tracks = self.update(t, hand(.2), hand(.7))
        self.assertEqual([x.side for x in tracks], ['left', 'right'])
        self.assertNotEqual(tracks[0].tracker_id, tracks[1].tracker_id)
        self.assertTrue(all(x.continuous for x in tracks))

    def test_empty_frame_returns_no_predicted_hand_and_breaks_continuity(self):
        self.update(0, hand(.2))
        before = self.update(100, hand(.2))[0]
        self.assertEqual(self.update(200), ())
        after = self.update(300, hand(.2))[0]
        self.assertEqual(before.tracker_id, after.tracker_id)
        self.assertFalse(after.continuous)

    def test_long_occlusion_expires_identity(self):
        self.update(0, hand(.2))
        first = self.update(100, hand(.2))[0].tracker_id
        for t in range(200, 1000, 100):
            self.update(t)
        self.assertIsNone(self.update(1000, hand(.2))[0].tracker_id)
        self.assertNotEqual(first, self.update(1100, hand(.2))[0].tracker_id)

    def test_sparse_timestamps_reset_instead_of_smoothing_over_gap(self):
        self.update(0, hand(.2))
        self.update(100, hand(.2))
        after = self.update(5000, hand(.2))[0]
        self.assertIsNone(after.tracker_id)
        self.assertEqual(after.generation, 1)
        self.assertFalse(after.continuous)

    def test_resolution_change_requires_explicit_reset(self):
        self.update(0, hand(.2))
        with self.assertRaisesRegex(TrackingError, 'resolution'):
            self.update(100, hand(.2), width=1280)
        self.tracker.reset()
        self.assertIsNone(self.update(0, hand(.2), width=1280)[0].tracker_id)

    def test_reset_does_not_inherit_previous_clip(self):
        self.update(0, hand(.2))
        self.update(100, hand(.2))
        self.tracker.reset()
        self.assertIsNone(self.update(0, hand(.2))[0].tracker_id)

    def test_detector_handedness_swap_breaks_continuity_not_relabels(self):
        for t in (0, 100, 200):
            before = self.update(t, hand(.2), hand(.7))
        after = self.update(300, hand(.7), hand(.2))
        self.assertEqual(after[0].side, 'left')
        self.assertEqual(after[0].tracker_id, before[1].tracker_id)
        self.assertFalse(after[0].continuous)
        self.assertFalse(after[1].continuous)

    def test_returns_input_order_even_if_library_reorders(self):
        original = self.tracker._tracker.update
        def reverse(*args, **kwargs):
            result = original(*args, **kwargs)
            return result[np.arange(len(result) - 1, -1, -1)]
        reference = HolisticHandTracker()
        with patch.object(self.tracker._tracker, 'update', side_effect=reverse):
            for t in (0, 100):
                out = self.update(t, hand(.2), hand(.7))
                expected = reference.update(features(hand(.2), hand(.7)), timestamp_ms=t,
                                            width=640, height=480)
                self.assertEqual(out, expected)
        self.assertEqual([x.side for x in out], ['left', 'right'])
        self.assertEqual(out[0].tracker_id, 0)  # Zero must not be mistaken for absence.
        self.assertNotEqual(out[0].tracker_id, out[1].tracker_id)

    def test_landmarks_are_never_modified(self):
        data = features(hand(.2), hand(.7))
        before = data.copy()
        self.tracker.update(data, timestamp_ms=0, width=640, height=480)
        np.testing.assert_array_equal(data, before)

    def test_rejects_bad_shapes_types_and_partial_missing(self):
        bad = [np.zeros((553, 3)), np.zeros((543, 2)), np.zeros((543, 3), dtype=bool),
               np.full((543, 3), '0'), features(hand(.2))]
        bad[-1][468, 0] = np.nan
        for item in bad:
            with self.subTest(shape=item.shape, dtype=item.dtype):
                with self.assertRaises(TrackingError):
                    self.tracker.update(item, timestamp_ms=0, width=640, height=480)
        self.assertIsNone(self.update(0, hand(.2))[0].tracker_id)

    def test_rejects_inf_degenerate_and_unbounded_geometry(self):
        for points in ([[0, 0, 0]] * 21, hand(float('inf')), hand(1e20)):
            with self.assertRaises(TrackingError):
                self.update(0, points)

    def test_timestamps_are_strict_and_bad_frame_does_not_advance_state(self):
        self.update(100, hand(.2))
        for value in (100, 99, -1, float('nan'), float('inf'), True, '200', 10 ** 400):
            with self.assertRaises(TrackingError):
                self.update(value, hand(.2))
        self.assertIsNotNone(self.update(200, hand(.2))[0].tracker_id)

    def test_late_input_validation_is_transactional(self):
        self.update(0, hand(.2), hand(.7))
        with self.assertRaises(TrackingError):
            self.update(100, hand(.2), [[0, 0, 0]] * 21)
        self.assertIsNotNone(self.update(100, hand(.2), hand(.7))[0].tracker_id)

    def test_int64_minimum_cannot_overflow_geometry_validation(self):
        data = np.zeros((543, 3), dtype=np.int64)
        data[468:489] = np.asarray(hand(1))
        data[468:489, 2] = np.iinfo(np.int64).min
        with self.assertRaises(TrackingError):
            self.tracker.update(data, timestamp_ms=0, width=640, height=480)

    def test_invalid_library_mapping_resets_state(self):
        original = self.tracker._tracker.update
        def fractional(*args, **kwargs):
            result = original(*args, **kwargs)
            result.data['input_row'] = np.array([.9])
            result.tracker_id = np.array([1.9])
            return result
        with patch.object(self.tracker._tracker, 'update', side_effect=fractional):
            with self.assertRaises(TrackingError):
                self.update(0, hand(.2))
        self.assertIsNone(self.update(0, hand(.2))[0].tracker_id)

    def test_no_network_during_real_library_run(self):
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('network')):
            for t in (0, 100, 200):
                self.update(t, hand(.2), hand(.7))


class FileTests(unittest.TestCase):
    def test_document_does_not_emit_landmarks_or_boxes(self):
        doc = document([frame(0, hand(.2)), frame(100, hand(.2)), frame(200, hand(.2))])
        before = copy.deepcopy(doc)
        result = track_document(doc)
        self.assertEqual(doc, before)
        self.assertEqual(result['tracker']['package'], 'trackers')
        self.assertEqual(result['tracker']['version'], '2.7.0')
        self.assertEqual(set(result['frames'][2]['hands'][0]), {'side', 'generation', 'tracker_id', 'continuous'})
        self.assertTrue(result['frames'][2]['hands'][0]['continuous'])

    def test_rejects_schema_extras_and_nonfinite(self):
        for change in ({'schema_version': True}, {'extra': 'no'}, {'width': True},
                       {'frames': []}, {'width': 0}):
            with self.assertRaises(TrackingError):
                track_document(document([frame(0)]) | change)
        for bad_hand in ([[float('nan'), 0, 0]] * 21, [[False, 0, 0]] * 21):
            with self.assertRaises(TrackingError):
                track_document(document([frame(0, bad_hand)]))

    def test_file_writes_fresh_summary_only_after_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'input.json', Path(tmp) / 'out.json'
            source.write_text(json.dumps(document([frame(0)])))
            run_file(source, target)
            result = json.loads(target.read_text())
            self.assertEqual(len(result['input_sha256']), 64)
            self.assertEqual(result['frames'][0]['hands'], [])
            with self.assertRaises(TrackingError):
                run_file(source, target)
            self.assertEqual(json.loads(target.read_text()), result)

    def test_rejects_duplicate_keys_and_nonstandard_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'input.json', Path(tmp) / 'out.json'
            for raw in ('{"schema_version":1,"schema_version":1}', '{"bad":NaN}'):
                source.write_text(raw)
                with self.assertRaises(TrackingError):
                    run_file(source, target)
                self.assertFalse(target.exists())

    def test_bounds_file_size_and_frame_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'input.json', Path(tmp) / 'out.json'
            source.write_bytes(b' ' * (4 * 1024 * 1024 + 1))
            with self.assertRaises(TrackingError):
                run_file(source, target)
        with self.assertRaises(TrackingError):
            track_document(document([frame(i) for i in range(1801)]))

    def test_real_cli_success_and_invalid_input_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'input.json', Path(tmp) / 'out.json'
            source.write_text(json.dumps(document([frame(0, hand(.2)), frame(100, hand(.2))])))
            command = [sys.executable, str(Path(__file__).with_name('hand_tracking.py')),
                       str(source), str(target)]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(target.read_text())['frames'][1]['hands'][0]['tracker_id'], 0)
            target.unlink()
            source.write_text(json.dumps(document([frame(10 ** 400)])))
            completed = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertEqual(completed.returncode, 2)
            self.assertNotIn('Traceback', completed.stderr)
            self.assertNotIn(str(10 ** 400), completed.stderr)
            self.assertFalse(target.exists())

    def test_disk_failure_never_publishes_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'input.json', Path(tmp) / 'out.json'
            source.write_text(json.dumps(document([frame(0)])))
            with patch('hand_tracking.os.fsync', side_effect=OSError('disk full')):
                with self.assertRaises(TrackingError):
                    run_file(source, target)
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [source])

    def test_publish_race_never_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'input.json', Path(tmp) / 'out.json'
            source.write_text(json.dumps(document([frame(0)])))
            import os
            original = os.link
            def create_existing(source, destination):
                Path(destination).write_text('existing')
                original(source, destination)
            with patch('hand_tracking.os.link', side_effect=create_existing):
                with self.assertRaises(TrackingError):
                    run_file(source, target)
            self.assertEqual(target.read_text(), 'existing')
            self.assertEqual(len(list(Path(tmp).iterdir())), 2)

    def test_existing_symlink_output_is_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'input.json', Path(tmp) / 'out.json'
            source.write_text(json.dumps(document([frame(0)])))
            target.symlink_to(Path(tmp) / 'missing.json')
            with self.assertRaises(TrackingError):
                run_file(source, target)
            self.assertFalse((Path(tmp) / 'missing.json').exists())


if __name__ == '__main__':
    unittest.main()
