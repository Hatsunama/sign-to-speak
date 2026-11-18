package com.xmilo_at_your_side.sign_to_speak;

/** Monotonic timing for the phone preview QA button search. */
final class ButtonSettleDeadline {
    private static final long SEARCH_MS = 15_000;
    private static final long SETTLE_LIMIT_MS = 17_000;
    private static final long STABLE_MS = 400;

    private final long searchDeadline;
    private final long settleDeadline;
    private long stableSince = -1;

    ButtonSettleDeadline(long startedAt) {
        searchDeadline = startedAt + SEARCH_MS;
        settleDeadline = startedAt + SETTLE_LIMIT_MS;
    }

    boolean canSearch(long now) {
        return now < searchDeadline;
    }

    boolean canSample(long now) {
        // Only a candidate still ready at the end of acquisition can use the extra time.
        return canSearch(now) || (stableSince >= 0 && now < settleDeadline);
    }

    long stableSince() {
        return stableSince;
    }

    boolean observeReady(long now, boolean geometryChanged) {
        if (!canSample(now)) return false;
        if (stableSince < 0 || geometryChanged) stableSince = now;
        return now - stableSince >= STABLE_MS;
    }

    boolean acceptFreshValidation(long now, boolean freshReady) {
        if (freshReady && canSample(now) && stableSince >= 0 && now - stableSince >= STABLE_MS) {
            return true;
        }
        clearStability();
        return false;
    }

    void clearStability() {
        stableSince = -1;
    }
}
