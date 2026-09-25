import { dist, type Point } from "./geometry";

/**
 * InstantHMR / MHR70 hand joints, proximal to distal, in MediaPipe order:
 * wrist, thumb, index, middle, ring, pinky.
 *
 * Finger names in the checkpoint run tip → first → second → third, and the
 * third joint sits nearest the wrist. See momolesang/InstantHMR skeleton.py.
 */
const RIGHT_WRIST = 41;
const LEFT_WRIST = 62;

const RIGHT_FINGERS = [
  [24, 23, 22, 21],
  [28, 27, 26, 25],
  [32, 31, 30, 29],
  [36, 35, 34, 33],
  [40, 39, 38, 37],
] as const;

const LEFT_FINGERS = [
  [45, 44, 43, 42],
  [49, 48, 47, 46],
  [53, 52, 51, 50],
  [57, 56, 55, 54],
  [61, 60, 59, 58],
] as const;

export type HandSide = "left" | "right";

export function handFromMhr(joints: Point[], side: HandSide): Point[] | null {
  if (joints.length < 70) {
    return null;
  }
  const wristIndex = side === "right" ? RIGHT_WRIST : LEFT_WRIST;
  const fingers = side === "right" ? RIGHT_FINGERS : LEFT_FINGERS;
  const wrist = joints[wristIndex];
  if (!wrist) {
    return null;
  }
  const hand: Point[] = [wrist];
  for (const chain of fingers) {
    for (const index of chain) {
      const point = joints[index];
      if (!point) {
        return null;
      }
      hand.push(point);
    }
  }
  return hand.length === 21 ? hand : null;
}

export function selectSigningHand(
  joints: Point[],
): { side: HandSide; hand: Point[] } | null {
  let best: { side: HandSide; hand: Point[]; reach: number } | null = null;
  for (const side of ["right", "left"] as const) {
    const hand = handFromMhr(joints, side);
    if (!hand) {
      continue;
    }
    const reach = handReach(hand);
    if (!best || reach > best.reach) {
      best = { side, hand, reach };
    }
  }
  if (!best || best.reach < 0.04) {
    return null;
  }
  return { side: best.side, hand: best.hand };
}

function handReach(hand: Point[]): number {
  const wrist = hand[0];
  if (!wrist) {
    return 0;
  }
  let reach = 0;
  for (const index of [4, 8, 12, 16, 20]) {
    const tip = hand[index];
    if (tip) {
      reach += dist(wrist, tip);
    }
  }
  return reach;
}

export function mhrJointsToPoints(
  joints: ArrayLike<number>,
  layout: "xyz" | "xy" = "xyz",
): Point[] {
  const stride = layout === "xyz" ? 3 : 2;
  const count = Math.floor(joints.length / stride);
  const points: Point[] = [];
  for (let index = 0; index < count; index += 1) {
    const offset = index * stride;
    points.push({
      x: joints[offset] ?? 0,
      y: joints[offset + 1] ?? 0,
      z: layout === "xyz" ? (joints[offset + 2] ?? 0) : 0,
    });
  }
  return points;
}
