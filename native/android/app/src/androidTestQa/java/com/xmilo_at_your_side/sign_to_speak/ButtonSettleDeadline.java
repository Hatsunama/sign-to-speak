package com.xmilo_at_your_side.sign_to_speak;

/** Monotonic timing for the phone preview QA button search. */
final class ButtonSettleDeadline {
    private static final long SEARCH_MS = 15_000;
    private static final long STABLE_MS = 400;

    private final long searchDeadline;
    private long stableSince = -1;

    ButtonSettleDeadline(long startedAt) {
        searchDeadline = startedAt + SEARCH_MS;
    }

    boolean canSample(long now) {
        return now < searchDeadline;
    }

    long stableSince() {
        return stableSince;
    }

    boolean observeReady(long now, boolean geometryChanged) {
        if (stableSince < 0 || geometryChanged) stableSince = now;
        return now - stableSince >= STABLE_MS;
    }

    void clearStability() {
        stableSince = -1;
    }
}
