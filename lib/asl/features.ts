import { angleAt, clamp, cross, dist, dot, mag, norm, scale, sub, type Point } from "./geometry";

export type FingerName = "thumb" | "index" | "middle" | "ring" | "pinky";

export type HandFeatures = {
  palmSize: number;
  curl: Record<FingerName, number>;
  along: Record<Exclude<FingerName, "thumb">, number>;
  spreadIndexMiddle: number;
  spreadMiddleRing: number;
  spreadRingPinky: number;
  crossedIndexMiddle: boolean;
  thumbLateral: number;
  thumbDistal: number;
  thumbPalmar: number;
  thumbToIndex: number;
  thumbToMiddle: number;
  thumbToRing: number;
  thumbToPinky: number;
  fingertipsTogether: number;
};

const FINGER_JOINTS = {
  index: [5, 6, 7, 8],
  middle: [9, 10, 11, 12],
  ring: [13, 14, 15, 16],
  pinky: [17, 18, 19, 20],
} as const;

function fingerCurl(
  landmarks: Point[],
  mcp: number,
  pip: number,
  dip: number,
  tip: number,
): number {
  const pipAngle = angleAt(landmarks[mcp]!, landmarks[pip]!, landmarks[dip]!);
  const dipAngle = angleAt(landmarks[pip]!, landmarks[dip]!, landmarks[tip]!);
  return clamp((170 - (pipAngle + dipAngle) / 2) / 100, 0, 1);
}

function thumbCurl(landmarks: Point[]): number {
  const mcpAngle = angleAt(landmarks[1]!, landmarks[2]!, landmarks[3]!);
  const ipAngle = angleAt(landmarks[2]!, landmarks[3]!, landmarks[4]!);
  return clamp((170 - (mcpAngle * 0.35 + ipAngle * 0.65)) / 100, 0, 1);
}

function fingerDirection(landmarks: Point[], mcp: number, tip: number): Point {
  return norm(sub(landmarks[tip]!, landmarks[mcp]!));
}

function angleBetween(a: Point, b: Point): number {
  const denom = mag(a) * mag(b);
  if (denom < 1e-8) {
    return 0;
  }
  const cosine = clamp(dot(a, b) / denom, -1, 1);
  return (Math.acos(cosine) * 180) / Math.PI;
}

export function readFeatures(landmarks: Point[]): HandFeatures | null {
  if (landmarks.length < 21) {
    return null;
  }
  const wrist = landmarks[0];
  const middleMcp = landmarks[9];
  const thumbMcp = landmarks[2];
  if (!wrist || !middleMcp || !thumbMcp) {
    return null;
  }

  const up = norm(sub(middleMcp, wrist));
  let thumbSide = sub(thumbMcp, middleMcp);
  thumbSide = sub(thumbSide, scale(up, dot(thumbSide, up)));
  if (mag(thumbSide) < 1e-6) {
    return null;
  }
  thumbSide = norm(thumbSide);
  const palmar = norm(cross(thumbSide, up));
  const palmSize = Math.max(dist(wrist, middleMcp), 1e-4);

  const curl = {
    thumb: thumbCurl(landmarks),
    index: fingerCurl(landmarks, 5, 6, 7, 8),
    middle: fingerCurl(landmarks, 9, 10, 11, 12),
    ring: fingerCurl(landmarks, 13, 14, 15, 16),
    pinky: fingerCurl(landmarks, 17, 18, 19, 20),
  };

  const along = {
    index: dot(fingerDirection(landmarks, 5, 8), up),
    middle: dot(fingerDirection(landmarks, 9, 12), up),
    ring: dot(fingerDirection(landmarks, 13, 16), up),
    pinky: dot(fingerDirection(landmarks, 17, 20), up),
  };

  const indexDir = fingerDirection(landmarks, 5, 8);
  const middleDir = fingerDirection(landmarks, 9, 12);
  const ringDir = fingerDirection(landmarks, 13, 16);
  const pinkyDir = fingerDirection(landmarks, 17, 20);

  const indexMcpSide = dot(sub(landmarks[5]!, wrist), thumbSide);
  const middleMcpSide = dot(sub(landmarks[9]!, wrist), thumbSide);
  const indexTipSide = dot(sub(landmarks[8]!, wrist), thumbSide);
  const middleTipSide = dot(sub(landmarks[12]!, wrist), thumbSide);
  const mcpOrder = indexMcpSide - middleMcpSide;
  const tipOrder = indexTipSide - middleTipSide;
  const crossedIndexMiddle =
    mcpOrder * tipOrder < 0 && Math.abs(tipOrder) > palmSize * 0.08;

  const thumbTip = landmarks[4]!;
  const thumbFromMiddle = sub(thumbTip, middleMcp);

  const tips = [landmarks[8]!, landmarks[12]!, landmarks[16]!, landmarks[20]!];
  const centroid = tips.reduce<Point>(
    (sum, tip) => addPoint(sum, tip),
    { x: 0, y: 0, z: 0 },
  );
  const tipCenter = scale(centroid, 1 / tips.length);
  const fingertipsTogether =
    tips.reduce((sum, tip) => sum + dist(tip, tipCenter), 0) / tips.length / palmSize;

  return {
    palmSize,
    curl,
    along,
    spreadIndexMiddle: angleBetween(indexDir, middleDir),
    spreadMiddleRing: angleBetween(middleDir, ringDir),
    spreadRingPinky: angleBetween(ringDir, pinkyDir),
    crossedIndexMiddle,
    thumbLateral: dot(thumbFromMiddle, thumbSide) / palmSize,
    thumbDistal: dot(sub(thumbTip, wrist), up) / palmSize,
    thumbPalmar: dot(thumbFromMiddle, palmar) / palmSize,
    thumbToIndex: dist(thumbTip, landmarks[8]!) / palmSize,
    thumbToMiddle: dist(thumbTip, landmarks[12]!) / palmSize,
    thumbToRing: dist(thumbTip, landmarks[16]!) / palmSize,
    thumbToPinky: dist(thumbTip, landmarks[20]!) / palmSize,
    fingertipsTogether,
  };
}

function addPoint(a: Point, b: Point): Point {
  return { x: a.x + b.x, y: a.y + b.y, z: a.z + b.z };
}

export const FINGER_INDEX = FINGER_JOINTS;
