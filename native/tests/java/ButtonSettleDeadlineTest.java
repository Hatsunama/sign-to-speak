package com.xmilo_at_your_side.sign_to_speak;

public final class ButtonSettleDeadlineTest {
    public static void main(String[] args) {
        lateReadyButtonCanReachFreshValidation();
        acquisitionExpiresWithoutAReadyCandidate();
        firstReadyAfterAcquisitionExpiresCannotStartSettlement();
        geometryChangeRestartsStabilityWithoutExtendingTheCap();
        losingReadinessEndsTheSettlementAllowance();
        failedFreshValidationClearsStability();
        freshValidationMustFinishBeforeTheHardCap();
        normalAcquisitionCanRecoverAndSettle();
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
        check(deadline.acceptFreshValidation(stableSample + 1, true), "matching validation can finish in grace");
    }

    private static void acquisitionExpiresWithoutAReadyCandidate() {
        ButtonSettleDeadline deadline = new ButtonSettleDeadline(0);
        check(deadline.canSearch(14_999), "gestures remain available inside acquisition");
        // Missing, disabled, and clipped buttons never produce a fully ready sample.
        deadline.clearStability();
        check(!deadline.canSearch(15_000), "gestures stop at the acquisition deadline");
        check(!deadline.canSample(15_000), "a button that was never ready receives no grace");
    }

    private static void firstReadyAfterAcquisitionExpiresCannotStartSettlement() {
        ButtonSettleDeadline deadline = new ButtonSettleDeadline(0);
        check(deadline.canSample(14_999), "the in-flight sample can begin before cutoff");
        check(!deadline.observeReady(15_000, false), "readiness observed at cutoff is too late");
        check(deadline.stableSince() < 0, "an in-flight sample cannot arm grace after cutoff");
        check(!deadline.canSample(15_001), "late readiness cannot reopen acquisition");
    }

    private static void geometryChangeRestartsStabilityWithoutExtendingTheCap() {
        ButtonSettleDeadline deadline = new ButtonSettleDeadline(0);
        check(!deadline.observeReady(14_900, false), "the initial sample starts settlement");
        check(deadline.canSample(15_100), "the existing candidate can still be sampled");
        check(!deadline.canSearch(15_100), "settlement grace cannot authorize gestures");
        check(!deadline.observeReady(15_100, true), "movement restarts settlement");
        check(!deadline.observeReady(15_499, false), "399 ms after movement is too early");
        check(deadline.observeReady(15_500, false), "400 ms after movement reaches validation");
        check(!deadline.observeReady(16_800, true), "a late movement restarts the clock");
        check(!deadline.canSample(17_000), "movement cannot extend the absolute deadline");
        check(!deadline.observeReady(17_200, false), "stability achieved after the cap is rejected");
    }

    private static void losingReadinessEndsTheSettlementAllowance() {
        ButtonSettleDeadline deadline = new ButtonSettleDeadline(0);
        check(!deadline.observeReady(14_900, false), "the ready sample starts settlement");
        check(deadline.canSample(15_050), "the original candidate can be checked during grace");
        deadline.clearStability();
        check(deadline.stableSince() < 0, "loss clears the old stable time");
        check(!deadline.canSample(15_100), "loss after cutoff closes the settlement allowance");
        check(!deadline.observeReady(15_100, false), "reappearance cannot start a new allowance");
    }

    private static void failedFreshValidationClearsStability() {
        ButtonSettleDeadline deadline = new ButtonSettleDeadline(0);
        deadline.observeReady(14_800, false);
        check(deadline.observeReady(15_200, false), "settlement can reach fresh validation in grace");
        check(!deadline.acceptFreshValidation(15_250, false), "failed fresh validation is rejected");
        check(deadline.stableSince() < 0, "failed validation clears the old stable time");
        check(!deadline.canSample(15_251), "failed validation cannot renew grace");
    }

    private static void freshValidationMustFinishBeforeTheHardCap() {
        ButtonSettleDeadline deadline = new ButtonSettleDeadline(0);
        deadline.observeReady(14_900, false);
        check(deadline.observeReady(16_950, false), "validation can start shortly before the cap");
        check(deadline.canSample(16_999), "the original candidate can settle up to the cap");
        check(!deadline.acceptFreshValidation(17_000, true), "matching validation at the cap is rejected");
        check(!deadline.acceptFreshValidation(17_001, true), "a late result cannot resurrect the candidate");
    }

    private static void normalAcquisitionCanRecoverAndSettle() {
        ButtonSettleDeadline deadline = new ButtonSettleDeadline(10_000);
        check(!deadline.observeReady(12_000, false), "the first ready sample starts settlement");
        check(deadline.observeReady(12_400, false), "normal settlement takes 400 ms");
        check(!deadline.acceptFreshValidation(12_410, false), "a fresh mismatch clears stability");
        check(deadline.canSample(12_420), "the acquisition window remains open");
        check(!deadline.observeReady(12_420, false), "the replacement cannot inherit stability");
        check(!deadline.observeReady(12_819, false), "the replacement must settle for at least 400 ms");
        check(deadline.observeReady(12_820, false), "the replacement settles after 400 ms");
        check(deadline.acceptFreshValidation(12_830, true), "matching fresh validation still succeeds");
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
