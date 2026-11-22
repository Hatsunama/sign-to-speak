package com.xmilo_at_your_side.sign_to_speak;

public final class ButtonScrollTargetTest {
    public static void main(String[] args) {
        samsungReplyLayoutClippingGetsOneBoundedCorrection();
        clippingAboveTheViewportCorrectsUpward();
        fullyVisibleButtonDoesNotMove();
    }

    private static void samsungReplyLayoutClippingGetsOneBoundedCorrection() {
        int fromBeforeBlockingGesture = ButtonScrollTarget.forGeometry(
                308, 2850, 3006, 103, 2196);
        int target = ButtonScrollTarget.forGeometry(1026, 2132, 2288, 103, 2196);
        check(fromBeforeBlockingGesture == target,
                "the pre-gesture geometry must reach the same fully visible target in one move");
        check(target > 1026, "a button clipped below the viewport must scroll toward later content");
        int correctedBottom = 2288 - (target - 1026);
        check(correctedBottom < 2196,
                "the exact Samsung Reply-card geometry must finish inside the viewport");
    }

    private static void clippingAboveTheViewportCorrectsUpward() {
        int target = ButtonScrollTarget.forGeometry(800, 80, 236, 103, 2196);
        check(target < 800, "a button clipped above the viewport must scroll toward earlier content");
        int correctedTop = 80 + (800 - target);
        check(correctedTop > 103, "the corrected button must finish inside the top edge");
    }

    private static void fullyVisibleButtonDoesNotMove() {
        check(ButtonScrollTarget.forGeometry(1026, 500, 656, 103, 2196) == 1026,
                "stable visible geometry must not be disturbed");
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
