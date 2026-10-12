package com.xmilo_at_your_side.sign_to_speak;

/** Payload-free cancellation token for asynchronous native camera acquisition. */
public final class PreviewLease {
    private long generation = 0;
    private boolean active = false;

    public synchronized long begin(boolean permissionGranted, boolean foreground) {
        stop();
        if (!permissionGranted || !foreground) return -1;
        active = true;
        return generation;
    }

    public synchronized boolean isCurrent(long token) {
        return active && token == generation;
    }

    public synchronized void stop() {
        active = false;
        ++generation;
    }
}
