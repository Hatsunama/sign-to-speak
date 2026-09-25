import { clamp } from "./geometry";
import { readFeatures, type HandFeatures } from "./features";
import type { Point } from "./geometry";

export type GlossId =
  | "A"
  | "B"
  | "C"
  | "D"
  | "E"
  | "F"
  | "G"
  | "H"
  | "I"
  | "J"
  | "K"
  | "L"
  | "M"
  | "N"
  | "O"
  | "P"
  | "Q"
  | "R"
  | "S"
  | "T"
  | "U"
  | "V"
  | "W"
  | "X"
  | "Y"
  | "Z"
  | "SPACE"
  | "YES"
  | "NO"
  | "ILY";

export type RankedGloss = {
  id: GlossId;
  score: number;
};

const MIN_SCORE = 0.62;

export function classifyHand(landmarks: Point[]): RankedGloss | null {
  const ranked = rankHand(landmarks);
  const best = ranked[0];
  const second = ranked[1];
  if (!best || best.score < MIN_SCORE) {
    return null;
  }
  const margin = best.score - (second?.score ?? 0);
  if (margin < 0.08) {
    return null;
  }
  return best;
}

export function rankHand(landmarks: Point[]): RankedGloss[] {
  const features = readFeatures(landmarks);
  if (!features) {
    return [];
  }
  const scores = scoreAll(features);
  return Object.entries(scores)
    .map(([id, score]) => ({ id: id as GlossId, score }))
    .sort((a, b) => b.score - a.score);
}

export function glossLabel(id: GlossId): string {
  switch (id) {
    case "SPACE":
      return "space";
    case "YES":
      return "yes";
    case "NO":
      return "no";
    case "ILY":
      return "I love you";
    case "A":
    case "B":
    case "C":
    case "D":
    case "E":
    case "F":
    case "G":
    case "H":
    case "I":
    case "J":
    case "K":
    case "L":
    case "M":
    case "N":
    case "O":
    case "P":
    case "Q":
    case "R":
    case "S":
    case "T":
    case "U":
    case "V":
    case "W":
    case "X":
    case "Y":
    case "Z":
      return id;
    default: {
      const neverId: never = id;
      return neverId;
    }
  }
}

export function appendGloss(text: string, id: GlossId): string {
  if (id === "SPACE") {
    if (text.length === 0 || text.endsWith(" ")) {
      return text;
    }
    return `${text} `;
  }
  if (id === "YES" || id === "NO" || id === "ILY") {
    const word = glossLabel(id);
    const base = text.length === 0 || text.endsWith(" ") ? text : `${text} `;
    return `${base}${word} `;
  }
  return `${text}${id}`;
}

function scoreAll(features: HandFeatures): Record<string, number> {
  const index = features.curl.index;
  const middle = features.curl.middle;
  const ring = features.curl.ring;
  const pinky = features.curl.pinky;
  const straight = {
    index: extended(index),
    middle: extended(middle),
    ring: extended(ring),
    pinky: extended(pinky),
  };
  const shut = {
    index: curled(index),
    middle: curled(middle),
    ring: curled(ring),
    pinky: curled(pinky),
  };
  const up = {
    index: pointingUp(features.along.index),
    middle: pointingUp(features.along.middle),
    ring: pointingUp(features.along.ring),
    pinky: pointingUp(features.along.pinky),
  };
  const side = {
    index: pointingSide(features.along.index),
    middle: pointingSide(features.along.middle),
  };
  const down = {
    index: pointingDown(features.along.index),
    middle: pointingDown(features.along.middle),
  };
  const fourShut = shut.index * shut.middle * shut.ring * shut.pinky;
  const thumbOut = high(features.thumbLateral, 0.72, 0.95);
  const thumbLow = low(features.thumbDistal, 0.28, 0.5);
  const together = low(features.spreadIndexMiddle, 12, 28);
  const apart = high(features.spreadIndexMiddle, 32, 55);
  const open = features.crossedIndexMiddle ? 0 : 1;

  return {
    B: shape(
      [straight.index, straight.middle, straight.ring, straight.pinky, together],
      [up.index, up.middle, low(features.thumbLateral, 0.62, 0.8)],
    ),
    SPACE: shape(
      [straight.index, straight.middle, straight.ring, straight.pinky, apart, thumbOut],
      [up.middle],
    ),
    W: shape(
      [straight.index, straight.middle, straight.ring, shut.pinky],
      [up.index, up.middle, high(features.spreadIndexMiddle, 28, 50)],
    ),
    F: shape(
      [straight.middle, straight.ring, straight.pinky, low(features.thumbToIndex, 0.22, 0.4)],
      [curled(index, 0.5), up.middle],
    ),
    U: shape(
      [straight.index, straight.middle, shut.ring, shut.pinky, together, open],
      [up.index, up.middle],
    ),
    R: shape(
      [straight.index, straight.middle, features.crossedIndexMiddle ? 1 : 0],
      [shut.ring, shut.pinky],
    ),
    V: shape(
      [straight.index, straight.middle, apart, up.middle, open],
      [shut.ring, shut.pinky, low(features.thumbDistal, 0.85, 1.05)],
    ),
    K: shape(
      [straight.index, straight.middle, apart, high(features.thumbDistal, 1.05, 1.25), open],
      [shut.ring, shut.pinky],
    ),
    H: shape(
      [straight.index, straight.middle, side.index, side.middle, together],
      [shut.ring, shut.pinky],
    ),
    P: shape(
      [straight.index, straight.middle, down.index, down.middle],
      [shut.ring, shut.pinky, apart],
    ),
    L: shape(
      [straight.index, up.index, thumbOut, thumbLow],
      [shut.middle, shut.ring, shut.pinky],
    ),
    G: shape(
      [straight.index, side.index, thumbOut, thumbLow],
      [shut.middle, shut.ring, shut.pinky],
    ),
    D: shape(
      [straight.index, up.index, low(features.thumbToMiddle, 0.28, 0.5)],
      [shut.middle, shut.ring, shut.pinky, low(features.thumbLateral, 0.55, 0.75)],
    ),
    Q: shape(
      [straight.index, down.index, low(features.thumbDistal, -0.2, 0.05)],
      [shut.middle, shut.ring, shut.pinky],
    ),
    X: shape(
      [band(index, 0.41, 0.2), shut.middle, shut.ring, shut.pinky],
      [low(Math.abs(features.along.index), 0.45, 0.75)],
    ),
    I: shape(
      [straight.pinky, up.pinky, high(features.thumbDistal, 0.7, 0.9)],
      [shut.index, shut.middle, shut.ring, low(features.thumbLateral, 0.78, 0.9)],
    ),
    Y: shape(
      [straight.pinky, up.pinky, thumbOut, thumbLow],
      [shut.index, shut.middle, shut.ring],
    ),
    ILY: shape(
      [straight.index, straight.pinky, thumbOut, up.index],
      [shut.middle, shut.ring, up.pinky],
    ),
    C: shape(
      [band(index, 0.38, 0.16), band(middle, 0.38, 0.16), high(features.thumbToMiddle, 0.55, 0.8)],
      [band(ring, 0.4, 0.16), band(pinky, 0.42, 0.16)],
    ),
    O: shape(
      [
        band(index, 0.53, 0.12),
        band(middle, 0.53, 0.12),
        low(features.thumbToMiddle, 0.32, 0.48),
      ],
      [high(features.thumbToMiddle, 0.12, 0.18)],
    ),
    E: shape(
      [band(index, 0.68, 0.12), band(middle, 0.68, 0.12), low(features.thumbToMiddle, 0.16, 0.28)],
      [band(ring, 0.68, 0.14)],
    ),
    YES: shape([fourShut, high(features.thumbDistal, 1.6, 1.9)], []),
    NO: shape([fourShut, low(features.thumbDistal, -0.25, -0.05)], []),
    T: shape([fourShut, band(features.thumbDistal, 1.27, 0.22)], []),
    A: shape(
      [fourShut, low(features.thumbPalmar, -0.28, -0.08)],
      [low(features.thumbDistal, 1.15, 1.4)],
    ),
    S: shape(
      [fourShut, high(features.thumbPalmar, 0.12, 0.24)],
      [band(features.thumbDistal, 0.8, 0.28), high(features.thumbToPinky, 0.4, 0.55)],
    ),
    M: shape(
      [fourShut, low(features.thumbToPinky, 0.3, 0.42)],
      [low(features.thumbDistal, 0.7, 0.9), high(features.thumbPalmar, 0.15, 0.3)],
    ),
    N: shape(
      [fourShut, low(features.thumbToIndex, 0.4, 0.55), high(features.thumbToPinky, 0.45, 0.6)],
      [band(features.thumbDistal, 0.52, 0.25), low(features.thumbPalmar, 0.18, 0.3)],
    ),
  };
}

function shape(required: number[], support: number[]): number {
  if (required.length === 0) {
    return mean(support);
  }
  let product = 1;
  for (const value of required) {
    product *= clamp(value, 0, 1);
  }
  if (product === 0) {
    return 0;
  }
  const geometric = product ** (1 / required.length);
  const supportScore = support.length === 0 ? 1 : mean(support);
  return geometric * (0.4 + 0.6 * supportScore);
}

function mean(values: number[]): number {
  if (values.length === 0) {
    return 0;
  }
  let total = 0;
  for (const value of values) {
    total += clamp(value, 0, 1);
  }
  return total / values.length;
}

function extended(curl: number): number {
  return low(curl, 0.16, 0.38);
}

function curled(curl: number, start = 0.78): number {
  return high(curl, start, 0.94);
}

function pointingUp(along: number): number {
  return high(along, 0.55, 0.9);
}

function pointingDown(along: number): number {
  return high(-along, 0.35, 0.7);
}

function pointingSide(along: number): number {
  return low(Math.abs(along), 0.18, 0.45);
}

function high(value: number, start: number, end: number): number {
  if (end === start) {
    return value >= end ? 1 : 0;
  }
  return clamp((value - start) / (end - start), 0, 1);
}

function low(value: number, start: number, end: number): number {
  if (end === start) {
    return value <= start ? 1 : 0;
  }
  return clamp((end - value) / (end - start), 0, 1);
}

function band(value: number, center: number, radius: number): number {
  if (radius <= 0) {
    return value === center ? 1 : 0;
  }
  return clamp(1 - Math.abs(value - center) / radius, 0, 1);
}
