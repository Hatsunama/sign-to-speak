import type { GlossId } from "./classify";

const HOLD_MS = 280;
const RELEASE_MS = 220;

export class SignLatch {
  private candidate: GlossId | null = null;
  private since = 0;
  private held: GlossId | null = null;
  private releasedAt = 0;

  reset(): void {
    this.candidate = null;
    this.since = 0;
    this.held = null;
    this.releasedAt = 0;
  }

  update(label: GlossId | null, now: number): GlossId | null {
    if (!label) {
      this.candidate = null;
      if (this.held && this.releasedAt === 0) {
        this.releasedAt = now;
      }
      if (this.held && now - this.releasedAt > RELEASE_MS) {
        this.held = null;
        this.releasedAt = 0;
      }
      return null;
    }

    if (this.releasedAt !== 0) {
      if (now - this.releasedAt > RELEASE_MS) {
        this.held = null;
      }
      this.releasedAt = 0;
    }
    if (label !== this.candidate) {
      this.candidate = label;
      this.since = now;
      return null;
    }
    if (now - this.since < HOLD_MS || this.held === label) {
      return null;
    }
    this.held = label;
    return label;
  }
}
