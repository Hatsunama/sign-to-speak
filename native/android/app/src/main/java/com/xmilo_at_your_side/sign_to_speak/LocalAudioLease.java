package com.xmilo_at_your_side.sign_to_speak;

/** Process-local exclusion between protected playback and privacy-sensitive microphone capture. */
public final class LocalAudioLease {
  public final class Lease implements AutoCloseable {
    private final Object claimant;
    private boolean released;
    private Lease(Object claimant) { this.claimant = claimant; }
    @Override public void close() {
      synchronized (LocalAudioLease.this) {
        if (released) return;
        released = true;
        if (owner == claimant) owner = null;
      }
    }
  }
  private Object owner;
  public synchronized Lease tryAcquire(Object claimant) {
    if (claimant == null || owner != null) return null;
    owner = claimant;
    return new Lease(claimant);
  }
  public synchronized boolean isHeld() { return owner != null; }
}
