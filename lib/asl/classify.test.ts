import { describe, expect, it } from "vitest";
import { classifyHand, glossLabel, appendGloss } from "./classify";
import { frameCrop, jointToFrame } from "./crop";
import { jitter, rotateZ, type Point } from "./geometry";
import { SignLatch } from "./latch";
import { buildHandMesh } from "./mesh";
import { handFromMhr } from "./mhr";
import { MotionWatch } from "./motion";
import { poseFor, poseJ, poseZ, STATIC_SIGNS, type SignName } from "./poses";

describe("asl hand reader", () => {
  it("reads every static practice shape", () => {
    for (const sign of STATIC_SIGNS) {
      const reading = classifyHand(poseFor(sign));
      expect(reading?.id, sign).toBe(sign);
    }
  });

  it("keeps the letter after a small turn and a little noise", () => {
    for (const sign of STATIC_SIGNS) {
      const noisy = poseFor(sign).map((point, index) =>
        jitter(rotateZ(point, 18), 0.004, index + sign.length),
      );
      expect(classifyHand(noisy)?.id, sign).toBe(sign);
    }
  });

  it("builds a hand mesh for every shape", () => {
    for (const sign of STATIC_SIGNS) {
      const mesh = buildHandMesh(poseFor(sign));
      expect(mesh).not.toBeNull();
      expect(mesh!.positions.length).toBeGreaterThan(200);
      expect(Number.isFinite(mesh!.positions[0])).toBe(true);
      expect(mesh!.indices.length % 3).toBe(0);
    }
  });

  it("lifts only the hand out of a 70-joint pose", () => {
    const joints = Array.from({ length: 70 }, () => ({ x: 0, y: 0, z: 0 }));
    joints[41] = { x: 1, y: 0, z: 0 };
    joints[21] = { x: 1, y: 4, z: 0 };
    const hand = handFromMhr(joints, "right");
    expect(hand).toHaveLength(21);
    expect(hand?.[0]).toEqual({ x: 1, y: 0, z: 0 });
    expect(hand?.[4]).toEqual({ x: 1, y: 4, z: 0 });
  });

  it("maps the crop center back to the middle of the frame", () => {
    const crop = frameCrop(640, 480);
    const center = jointToFrame(0, 0, crop);
    expect(center.x).toBeCloseTo(320, 4);
    expect(center.y).toBeCloseTo(240, 4);
    expect(crop.cliff[0]).toBeCloseTo(0, 5);
    expect(crop.cliff[2]).toBeCloseTo(1, 5);
  });

  it("reads J and Z from the stroke, not the resting shape", () => {
    expect(readStroke(poseJ, "J")).toBe("J");
    expect(readStroke(poseZ, "Z")).toBe("Z");
  });

  it("commits a held letter once, then again after the hand leaves", () => {
    const latch = new SignLatch();
    expect(latch.update("A", 0)).toBeNull();
    expect(latch.update("A", 300)).toBe("A");
    expect(latch.update("A", 500)).toBeNull();
    expect(latch.update(null, 800)).toBeNull();
    expect(latch.update("A", 1100)).toBeNull();
    expect(latch.update("A", 1400)).toBe("A");
  });

  it("spells words and whole signs into a transcript", () => {
    let text = "";
    for (const id of ["H", "I", "SPACE", "YES"] as const) {
      text = appendGloss(text, id);
    }
    expect(text).toBe("HI yes ");
    expect(glossLabel("ILY")).toBe("I love you");
  });
});

function readStroke(pose: (phase: number) => Point[], expected: SignName | "J" | "Z"): string | null {
  const watch = new MotionWatch();
  let found: string | null = null;
  for (let step = 0; step <= 8; step += 1) {
    const landmarks = pose(step / 8);
    const resting = classifyHand(landmarks);
    const motion = watch.push(landmarks, resting?.id ?? null, step * 80);
    if (motion.gloss) {
      found = motion.gloss;
    }
  }
  expect(found).toBe(expected);
  return found;
}
