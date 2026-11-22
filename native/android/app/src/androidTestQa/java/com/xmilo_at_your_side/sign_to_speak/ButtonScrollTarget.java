package com.xmilo_at_your_side.sign_to_speak;

/** Computes a deterministic ScrollView offset that places a rendered QA action inside the viewport. */
final class ButtonScrollTarget {
    private static final int EDGE_MARGIN_PX = 24;

    private ButtonScrollTarget() {}

    static int forGeometry(int scrollY, int buttonTop, int buttonBottom,
            int viewportTop, int viewportBottom) {
        long target = scrollY;
        if (buttonTop < viewportTop) {
            target -= (long) viewportTop - buttonTop + EDGE_MARGIN_PX;
        } else if (buttonBottom > viewportBottom) {
            target += (long) buttonBottom - viewportBottom + EDGE_MARGIN_PX;
        }
        return (int) Math.max(0, Math.min(Integer.MAX_VALUE, target));
    }
}
