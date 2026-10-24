import unittest

from visual_overlay_bounds import exclude_visible_edge_panel, exclude_visible_system_bubble


EDGE = '''  Window #4 Window{abc u0 com.sec.android.app.launcher/com.samsung.app.honeyspace.edge.edgepanel.app.CocktailBarService}:
    mAttrs={(0,433)(80x342) gr=TOP RIGHT CENTER sim={adjust=pan} ty=2226 fmt=TRANSPARENT}
    isVisible=true
  Window #3 Window{def u0 com.example/.MainActivity}:
    isVisible=true
'''
BASE = [2, 105, 1078, 2194]
BUBBLE_WINDOW = '''  Window #6 Window{f800121 u0 Bubbles!}:
    mAttrs={(0,0)(fillxfill) ty=APPLICATION_OVERLAY fmt=TRANSLUCENT pkg=com.android.systemui}
    mHasSurface=true isVisible=true
'''
BUBBLE_INPUT = '''  4: name=f800121 Bubbles!, id=96118, displayId=0, inputConfig=NOT_FOCUSABLE | TRUSTED_OVERLAY, alpha=1, frame=[0,0][1200,2670], touchableRegion=[-44,122][208,374], ownerPid=2416, ownerUid=10153
  5: name=abcd SurfaceView[Bubbles!], id=22, displayId=0, inputConfig=NOT_VISIBLE, alpha=1, frame=[0,0][1200,2670], touchableRegion=[0,0][0,0], ownerPid=2416, ownerUid=10153
'''
BUBBLE_CLONE = '''  4: name=f800121 Bubbles!, id=-2147482751, displayId=5, inputConfig=NOT_FOCUSABLE | TRUSTED_OVERLAY | CLONE, alpha=1, frame=[0,0][1200,2670], touchableRegion=[0,122][208,374], ownerPid=2416, ownerUid=10153
'''
BUBBLE_CROP = [2, 112, 1198, 2596]


class OverlayBoundsTests(unittest.TestCase):
    def test_mediaprojection_clone_precedes_physical_bubble(self):
        crop, evidence = exclude_visible_system_bubble(
            BUBBLE_CROP, BUBBLE_WINDOW, BUBBLE_CLONE + BUBBLE_INPUT, 1200)
        self.assertEqual(crop, [210, 112, 1198, 2596])
        self.assertEqual(evidence['touchable_region'], [-44, 122, 208, 374])

    def test_clone_is_not_a_physical_display_input(self):
        for input_dump in (BUBBLE_CLONE,
                           BUBBLE_CLONE.replace('displayId=5', 'displayId=0')):
            with self.subTest(input_dump=input_dump), self.assertRaises(ValueError):
                exclude_visible_system_bubble(BUBBLE_CROP, BUBBLE_WINDOW,
                                              input_dump, 1200)

    def test_visible_system_bubble_excludes_left_touchable_region_for_full_height(self):
        crop, evidence = exclude_visible_system_bubble(BUBBLE_CROP, BUBBLE_WINDOW, BUBBLE_INPUT, 1200)
        self.assertEqual(crop, [210, 112, 1198, 2596])
        self.assertEqual(evidence['touchable_region'], [-44, 122, 208, 374])
        self.assertEqual(evidence['side'], 'left')

    def test_background_surface_view_is_not_a_bubble_window(self):
        crop, evidence = exclude_visible_system_bubble(BUBBLE_CROP, BUBBLE_WINDOW.replace('isVisible=true', 'isVisible=false'), BUBBLE_INPUT, 1200)
        self.assertEqual(crop, BUBBLE_CROP)
        self.assertIsNone(evidence)

    def test_visible_bubble_requires_trusted_visible_matching_input(self):
        for input_dump in (BUBBLE_INPUT.replace('TRUSTED_OVERLAY', 'NOT_TRUSTED'),
                           BUBBLE_INPUT.replace('NOT_FOCUSABLE | TRUSTED_OVERLAY', 'NOT_VISIBLE | TRUSTED_OVERLAY'),
                           BUBBLE_INPUT.replace('name=f800121 Bubbles!', 'name=other Bubbles!')):
            with self.subTest(input_dump=input_dump), self.assertRaises(ValueError):
                exclude_visible_system_bubble(BUBBLE_CROP, BUBBLE_WINDOW, input_dump, 1200)

    def test_visible_bubble_requires_systemui_overlay_window(self):
        for window_dump in (BUBBLE_WINDOW.replace('com.android.systemui', 'com.example.app'),
                            BUBBLE_WINDOW.replace('APPLICATION_OVERLAY', 'APPLICATION'),
                            BUBBLE_WINDOW.replace('mHasSurface=true', 'mHasSurface=false')):
            with self.subTest(window_dump=window_dump), self.assertRaises(ValueError):
                exclude_visible_system_bubble(BUBBLE_CROP, window_dump, BUBBLE_INPUT, 1200)

    def test_central_multiple_or_oversized_bubble_fails_closed(self):
        cases = (BUBBLE_INPUT.replace('[-44,122][208,374]', '[350,122][550,374]'),
                 BUBBLE_INPUT.replace('[-44,122][208,374]', '[-44,122][500,374]'),
                 BUBBLE_INPUT.replace('touchableRegion=[-44,122][208,374]', 'touchableRegion=[-44,122][208,374] [300,122][400,374]'),
                 BUBBLE_INPUT + BUBBLE_INPUT.splitlines()[0].replace('f800121', 'f800122') + '\n')
        for input_dump in cases:
            with self.subTest(input_dump=input_dump), self.assertRaises(ValueError):
                exclude_visible_system_bubble(BUBBLE_CROP, BUBBLE_WINDOW, input_dump, 1200)

    def test_right_anchored_bubble_trims_right_side(self):
        input_dump = BUBBLE_INPUT.replace('[-44,122][208,374]', '[992,122][1244,374]')
        crop, evidence = exclude_visible_system_bubble(BUBBLE_CROP, BUBBLE_WINDOW, input_dump, 1200)
        self.assertEqual(crop, [2, 112, 990, 2596])
        self.assertEqual(evidence['side'], 'right')

    def test_bubble_input_frame_must_match_display(self):
        with self.assertRaisesRegex(ValueError, 'geometry'):
            exclude_visible_system_bubble(BUBBLE_CROP, BUBBLE_WINDOW,
                                          BUBBLE_INPUT.replace('frame=[0,0][1200,2670]', 'frame=[0,0][1080,2670]', 1), 1200)

    def test_visible_right_anchored_edge_panel_excludes_its_width_and_margin(self):
        crop, evidence = exclude_visible_edge_panel(BASE, EDGE)
        self.assertEqual(crop, [2, 105, 998, 2194])
        self.assertEqual(evidence['window'], 'com.sec.android.app.launcher/com.samsung.app.honeyspace.edge.edgepanel.app.CocktailBarService')
        self.assertEqual(evidence['attrs'], '(0,433)(80x342) gr=TOP RIGHT CENTER')
        self.assertEqual(evidence['width'], 80)
        self.assertEqual(evidence['excluded_from_x'], 998)

    def test_no_matching_visible_window_retains_crop(self):
        crop, evidence = exclude_visible_edge_panel(BASE, EDGE.replace('isVisible=true', 'isVisible=false', 1))
        self.assertEqual(crop, BASE)
        self.assertIsNone(evidence)

    def test_visible_matching_window_with_unknown_geometry_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'geometry'):
            exclude_visible_edge_panel(BASE, EDGE.replace('(80x342) gr=TOP RIGHT CENTER', '(MATCH_PARENTx342) gr=TOP RIGHT CENTER'))

    def test_visible_matching_window_without_right_anchor_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'geometry'):
            exclude_visible_edge_panel(BASE, EDGE.replace('gr=TOP RIGHT CENTER', 'gr=TOP LEFT CENTER'))

    def test_implausibly_wide_overlay_cannot_erase_app_roi(self):
        with self.assertRaisesRegex(ValueError, 'broad'):
            exclude_visible_edge_panel(BASE, EDGE.replace('(80x342)', '(900x342)'))


if __name__ == '__main__':
    unittest.main()
