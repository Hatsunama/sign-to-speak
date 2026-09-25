import { add, norm, scale, type Point } from "./geometry";

export type SignName =
  | "A"
  | "B"
  | "C"
  | "D"
  | "E"
  | "F"
  | "G"
  | "H"
  | "I"
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
  | "ILY"
  | "SPACE"
  | "YES"
  | "NO";

type Aim = "up" | "down" | "side";

const WRIST: Point = { x: 0, y: 0, z: 0 };
const INDEX_MCP: Point = { x: 0.024, y: 0.078, z: 0.002 };
const MIDDLE_MCP: Point = { x: 0, y: 0.088, z: 0 };
const RING_MCP: Point = { x: -0.02, y: 0.08, z: 0.002 };
const PINKY_MCP: Point = { x: -0.038, y: 0.066, z: 0.004 };

function aimVector(aim: Aim, lateral: number): Point {
  if (aim === "down") {
    return norm({ x: lateral, y: -1, z: 0 });
  }
  if (aim === "side") {
    return norm({ x: 1, y: lateral, z: 0 });
  }
  return norm({ x: lateral, y: 1, z: 0 });
}

function fingerChain(
  mcp: Point,
  curl: number,
  aim: Aim,
  lateral = 0,
): [Point, Point, Point, Point] {
  const base = aimVector(aim, lateral);
  const lengths = [0.03, 0.02, 0.016];
  let position = mcp;
  const points: Point[] = [mcp];
  let bend = 0;
  for (let index = 0; index < 3; index += 1) {
    bend += curl * (index === 0 ? 1.45 : 1.85);
    const forward = Math.cos(bend);
    const intoPalm = Math.sin(bend);
    const direction = norm({
      x: base.x * forward,
      y: base.y * forward,
      z: intoPalm,
    });
    position = add(position, scale(direction, lengths[index] ?? 0.02));
    points.push(position);
  }
  return [points[0], points[1], points[2], points[3]] as [
    Point,
    Point,
    Point,
    Point,
  ];
}

function thumbToward(target: Point, bend: number): [Point, Point, Point, Point] {
  const cmc: Point = { x: 0.018, y: 0.02, z: 0.012 };
  const mcp = add(cmc, scale(norm(add(subSafe(target, cmc), { x: 0, y: 0, z: bend })), 0.022));
  const ip = add(mcp, scale(norm(subSafe(target, mcp)), 0.02));
  return [cmc, mcp, ip, target];
}

function subSafe(a: Point, b: Point): Point {
  return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z };
}

function thumbExtended(aim: Aim): [Point, Point, Point, Point] {
  const cmc: Point = { x: 0.02, y: 0.016, z: 0.008 };
  const direction = aimVector(aim, aim === "side" ? 0.05 : 0.2);
  const mcp = add(cmc, scale(direction, 0.024));
  const ip = add(mcp, scale(direction, 0.02));
  const tip = add(ip, scale(direction, 0.018));
  return [cmc, mcp, ip, tip];
}

type FingerCurl = {
  index: number;
  middle: number;
  ring: number;
  pinky: number;
};

type PoseSpec = {
  curl: FingerCurl;
  aim?: Aim;
  aimed?: Array<"index" | "middle" | "ring" | "pinky">;
  spread?: number;
  cross?: boolean;
  thumb: Point | "up" | "down" | "side";
  thumbBend?: number;
};

const CLOSED: FingerCurl = { index: 1, middle: 1, ring: 1, pinky: 1 };

function specPose(spec: PoseSpec): Point[] {
  const aim = spec.aim ?? "up";
  const aimed = new Set(spec.aimed ?? ["index", "middle", "ring", "pinky"]);
  const spread = spec.spread ?? 0;
  const fingerAim = (name: "index" | "middle" | "ring" | "pinky"): Aim =>
    aimed.has(name) ? aim : "up";
  const lateral = (name: "index" | "middle" | "ring" | "pinky", base: number) => {
    if (spec.cross && (name === "index" || name === "middle")) {
      return name === "index" ? -0.95 : 0.95;
    }
    if (!aimed.has(name)) {
      return 0;
    }
    return base * spread;
  };

  const index = fingerChain(
    INDEX_MCP,
    spec.curl.index,
    fingerAim("index"),
    lateral("index", 1),
  );
  const middle = fingerChain(
    MIDDLE_MCP,
    spec.curl.middle,
    fingerAim("middle"),
    lateral("middle", -0.15),
  );
  const ring = fingerChain(
    RING_MCP,
    spec.curl.ring,
    fingerAim("ring"),
    lateral("ring", -1),
  );
  const pinky = fingerChain(
    PINKY_MCP,
    spec.curl.pinky,
    fingerAim("pinky"),
    lateral("pinky", -1.1),
  );

  const thumb =
    spec.thumb === "up" || spec.thumb === "down" || spec.thumb === "side"
      ? thumbExtended(spec.thumb)
      : thumbToward(spec.thumb, spec.thumbBend ?? 0);

  return [
    WRIST,
    ...thumb,
    ...index,
    ...middle,
    ...ring,
    ...pinky,
  ];
}

const besideIndex: Point = { x: 0.058, y: 0.082, z: -0.01 };
const acrossMiddle: Point = { x: -0.002, y: 0.07, z: 0.05 };
const betweenIndexMiddle: Point = { x: 0.012, y: 0.112, z: 0.014 };
const underMiddle: Point = { x: 0.012, y: 0.046, z: 0.024 };
const underRing: Point = { x: -0.034, y: 0.04, z: 0.022 };
const yesTip: Point = { x: 0.018, y: 0.18, z: 0 };
const meetTips: Point = { x: 0.006, y: 0.07, z: 0.03 };
const circlePoint: Point = { x: 0.008, y: 0.078, z: 0.028 };

function poseSpec(sign: SignName): PoseSpec {
  switch (sign) {
    case "A":
      return { curl: CLOSED, thumb: besideIndex, thumbBend: 0.2 };
    case "B":
      return {
        curl: { index: 0.05, middle: 0.05, ring: 0.05, pinky: 0.05 },
        spread: 0.08,
        thumb: { x: 0.03, y: 0.07, z: 0.03 },
      };
    case "C":
      return {
        curl: { index: 0.42, middle: 0.42, ring: 0.45, pinky: 0.48 },
        spread: 0.35,
        thumb: { x: 0.05, y: 0.06, z: 0.02 },
        thumbBend: 0.15,
      };
    case "D":
      return {
        curl: { index: 0.02, middle: 1, ring: 1, pinky: 1 },
        aimed: ["index"],
        thumb: { x: 0.002, y: 0.07, z: 0.026 },
      };
    case "E":
      return {
        curl: { index: 0.72, middle: 0.72, ring: 0.74, pinky: 0.76 },
        thumb: meetTips,
      };
    case "F":
      return {
        curl: { index: 0.7, middle: 0.04, ring: 0.04, pinky: 0.05 },
        aimed: ["middle", "ring", "pinky"],
        spread: 0.2,
        thumb: { x: 0.02, y: 0.075, z: 0.03 },
      };
    case "G":
      return {
        curl: { index: 0.04, middle: 1, ring: 1, pinky: 1 },
        aim: "side",
        aimed: ["index"],
        thumb: "side",
      };
    case "H":
      return {
        curl: { index: 0.04, middle: 0.04, ring: 1, pinky: 1 },
        aim: "side",
        aimed: ["index", "middle"],
        spread: 0.05,
        thumb: { x: 0.04, y: 0.04, z: 0.012 },
      };
    case "I":
      return {
        curl: { index: 1, middle: 1, ring: 1, pinky: 0.04 },
        aimed: ["pinky"],
        thumb: besideIndex,
      };
    case "K":
      return {
        curl: { index: 0.04, middle: 0.04, ring: 1, pinky: 1 },
        aimed: ["index", "middle"],
        spread: 1.1,
        thumb: betweenIndexMiddle,
      };
    case "L":
      return {
        curl: { index: 0.04, middle: 1, ring: 1, pinky: 1 },
        aimed: ["index"],
        thumb: "side",
      };
    case "M":
      return { curl: CLOSED, thumb: underRing };
    case "N":
      return { curl: CLOSED, thumb: underMiddle };
    case "O":
      return {
        curl: { index: 0.58, middle: 0.58, ring: 0.6, pinky: 0.62 },
        thumb: circlePoint,
      };
    case "P":
      return {
        curl: { index: 0.04, middle: 0.04, ring: 1, pinky: 1 },
        aim: "down",
        aimed: ["index", "middle"],
        spread: 1.1,
        thumb: { x: 0.012, y: -0.02, z: 0.02 },
      };
    case "Q":
      return {
        curl: { index: 0.05, middle: 1, ring: 1, pinky: 1 },
        aim: "down",
        aimed: ["index"],
        thumb: "down",
      };
    case "R":
      return {
        curl: { index: 0.05, middle: 0.05, ring: 1, pinky: 1 },
        aimed: ["index", "middle"],
        cross: true,
        thumb: { x: 0.03, y: 0.05, z: 0.02 },
      };
    case "S":
      return { curl: CLOSED, thumb: acrossMiddle };
    case "T":
      return { curl: CLOSED, thumb: betweenIndexMiddle };
    case "U":
      return {
        curl: { index: 0.04, middle: 0.04, ring: 1, pinky: 1 },
        aimed: ["index", "middle"],
        spread: 0.08,
        thumb: { x: 0.028, y: 0.05, z: 0.022 },
      };
    case "V":
      return {
        curl: { index: 0.04, middle: 0.04, ring: 1, pinky: 1 },
        aimed: ["index", "middle"],
        spread: 1.15,
        thumb: { x: 0.026, y: 0.045, z: 0.018 },
      };
    case "W":
      return {
        curl: { index: 0.04, middle: 0.04, ring: 0.04, pinky: 1 },
        aimed: ["index", "middle", "ring"],
        spread: 0.85,
        thumb: { x: 0.028, y: 0.05, z: 0.02 },
      };
    case "X":
      return {
        curl: { index: 0.48, middle: 1, ring: 1, pinky: 1 },
        aimed: ["index"],
        thumb: besideIndex,
      };
    case "Y":
      return {
        curl: { index: 1, middle: 1, ring: 1, pinky: 0.04 },
        aimed: ["pinky"],
        thumb: "side",
      };
    case "ILY":
      return {
        curl: { index: 0.04, middle: 1, ring: 1, pinky: 0.04 },
        aimed: ["index", "pinky"],
        thumb: "side",
      };
    case "SPACE":
      return {
        curl: { index: 0.02, middle: 0.02, ring: 0.02, pinky: 0.02 },
        spread: 0.9,
        thumb: "side",
      };
    case "YES":
      return { curl: CLOSED, thumb: yesTip };
    case "NO":
      return { curl: CLOSED, thumb: "down" };
    default: {
      const neverSign: never = sign;
      return neverSign;
    }
  }
}

export const STATIC_SIGNS: SignName[] = [
  "A",
  "B",
  "C",
  "D",
  "E",
  "F",
  "G",
  "H",
  "I",
  "K",
  "L",
  "M",
  "N",
  "O",
  "P",
  "Q",
  "R",
  "S",
  "T",
  "U",
  "V",
  "W",
  "X",
  "Y",
  "ILY",
  "SPACE",
  "YES",
  "NO",
];

export function poseFor(sign: SignName): Point[] {
  return specPose(poseSpec(sign));
}

/** Animated J: pinky draws a hook. Phase is 0..1. */
export function poseJ(phase: number): Point[] {
  const hand = poseFor("I");
  const t = Math.min(1, Math.max(0, phase));
  const offset =
    t < 0.45
      ? { x: 0, y: -(t / 0.45) * 0.06 }
      : { x: -((t - 0.45) / 0.55) * 0.05, y: -0.06 };
  const pinkyIndexes = [17, 18, 19, 20];
  return hand.map((point, index) => {
    if (!pinkyIndexes.includes(index)) {
      return point;
    }
    const weight = (index - 16) / 4;
    return {
      x: point.x + offset.x * weight,
      y: point.y + offset.y * weight,
      z: point.z,
    };
  });
}

/** Animated Z: index finger draws the three strokes. Phase is 0..1. */
export function poseZ(phase: number): Point[] {
  const hand = poseFor("D");
  const t = Math.min(1, Math.max(0, phase));
  let offset = { x: 0, y: 0 };
  if (t < 0.33) {
    offset = { x: -0.03 + (t / 0.33) * 0.06, y: 0.03 };
  } else if (t < 0.66) {
    const u = (t - 0.33) / 0.33;
    offset = { x: 0.03 - u * 0.06, y: 0.03 - u * 0.06 };
  } else {
    const u = (t - 0.66) / 0.34;
    offset = { x: -0.03 + u * 0.06, y: -0.03 };
  }
  const indexIndexes = [5, 6, 7, 8];
  return hand.map((point, index) => {
    if (!indexIndexes.includes(index)) {
      return point;
    }
    const weight = (index - 4) / 4;
    return {
      x: point.x + offset.x * weight,
      y: point.y + offset.y * weight,
      z: point.z,
    };
  });
}
