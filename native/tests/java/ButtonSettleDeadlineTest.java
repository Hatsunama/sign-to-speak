package com.xmilo_at_your_side.sign_to_speak;

public final class ButtonSettleDeadlineTest {
    public static void main(String[] args) {
        lateReadyButtonCanReachFreshValidation();
    }

    private static void lateReadyButtonCanReachFreshValidation() {
        // Model the Samsung failure: the final observed sample showed 278 ms of stability.
        long startedAt = 100_000;
        ButtonSettleDeadline deadline = new ButtonSettleDeadline(startedAt);
        long firstReady = startedAt + 14_650;
        check(deadline.canSample(firstReady), "initial ready sample is inside the search window");
        check(!deadline.observeReady(firstReady, false), "a first ready sample cannot settle");
        long lastSample = startedAt + 14_928;
        check(deadline.canSample(lastSample), "278 ms sample is inside the search window");
        check(!deadline.observeReady(lastSample, false), "278 ms is too early for fresh validation");
        long stableSample = startedAt + 15_050;
        check(deadline.canSample(stableSample),
                "a continuously ready button must be sampled at 400 ms even after the 15 s search window");
        check(deadline.observeReady(stableSample, false), "400 ms of matching samples allows fresh validation");
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
