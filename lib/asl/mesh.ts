import { cross, dist, norm, sub, type Point } from "./geometry";

const SIDES = 8;

const BONES: ReadonlyArray<readonly [number, number, number]> = [
  [1, 2, 1.05],
  [2, 3, 0.92],
  [3, 4, 0.78],
  [5, 6, 0.95],
  [6, 7, 0.82],
  [7, 8, 0.68],
  [9, 10, 1],
  [10, 11, 0.86],
  [11, 12, 0.72],
  [13, 14, 0.9],
  [14, 15, 0.78],
  [15, 16, 0.64],
  [17, 18, 0.78],
  [18, 19, 0.66],
  [19, 20, 0.54],
  [0, 1, 1.15],
  [0, 5, 1.2],
  [0, 9, 1.25],
  [0, 13, 1.15],
  [0, 17, 1.05],
];

export type HandMesh = {
  positions: Float32Array;
  indices: Uint32Array;
};

export function buildHandMesh(joints: Point[]): HandMesh | null {
  if (joints.length < 21) {
    return null;
  }
  const wrist = joints[0];
  const middle = joints[9];
  if (!wrist || !middle) {
    return null;
  }
  const palm = Math.max(dist(wrist, middle), 1e-4);
  const positions: number[] = [];
  const indices: number[] = [];

  const addVertex = (point: Point) => {
    positions.push(point.x, point.y, point.z);
    return positions.length / 3 - 1;
  };

  const pushTriangle = (a: number, b: number, c: number) => {
    indices.push(a, b, c);
  };

  for (const [start, end, radiusScale] of BONES) {
    const a = joints[start];
    const b = joints[end];
    if (!a || !b) {
      continue;
    }
    addTube(a, b, palm * 0.16 * radiusScale, addVertex, pushTriangle);
  }

  addPalm(joints, palm * 0.22, addVertex, pushTriangle);

  if (positions.length === 0 || indices.length === 0) {
    return null;
  }
  return {
    positions: Float32Array.from(positions),
    indices: Uint32Array.from(indices),
  };
}

function addTube(
  a: Point,
  b: Point,
  radius: number,
  addVertex: (point: Point) => number,
  pushTriangle: (a: number, b: number, c: number) => void,
) {
  const axis = norm(sub(b, a));
  if (axis.x === 0 && axis.y === 0 && axis.z === 0) {
    return;
  }
  const helper = Math.abs(axis.y) < 0.85 ? { x: 0, y: 1, z: 0 } : { x: 1, y: 0, z: 0 };
  const side = norm(cross(axis, helper));
  const up = norm(cross(side, axis));
  const ringA: number[] = [];
  const ringB: number[] = [];
  for (let step = 0; step < SIDES; step += 1) {
    const angle = (step / SIDES) * Math.PI * 2;
    const offset = {
      x: (side.x * Math.cos(angle) + up.x * Math.sin(angle)) * radius,
      y: (side.y * Math.cos(angle) + up.y * Math.sin(angle)) * radius,
      z: (side.z * Math.cos(angle) + up.z * Math.sin(angle)) * radius,
    };
    ringA.push(addVertex({ x: a.x + offset.x, y: a.y + offset.y, z: a.z + offset.z }));
    ringB.push(addVertex({ x: b.x + offset.x, y: b.y + offset.y, z: b.z + offset.z }));
  }
  for (let step = 0; step < SIDES; step += 1) {
    const next = (step + 1) % SIDES;
    const a0 = ringA[step];
    const a1 = ringA[next];
    const b0 = ringB[step];
    const b1 = ringB[next];
    if (a0 === undefined || a1 === undefined || b0 === undefined || b1 === undefined) {
      continue;
    }
    pushTriangle(a0, b0, a1);
    pushTriangle(a1, b0, b1);
  }
}

function addPalm(
  joints: Point[],
  thickness: number,
  addVertex: (point: Point) => number,
  pushTriangle: (a: number, b: number, c: number) => void,
) {
  const wrist = joints[0];
  const index = joints[5];
  const pinky = joints[17];
  if (!wrist || !index || !pinky) {
    return;
  }
  const normal = norm(cross(sub(index, wrist), sub(pinky, wrist)));
  const lift = {
    x: normal.x * thickness,
    y: normal.y * thickness,
    z: normal.z * thickness,
  };
  const palmIndexes = [0, 5, 9, 13, 17, 1];
  const top: number[] = [];
  const bottom: number[] = [];
  for (const index of palmIndexes) {
    const point = joints[index];
    if (!point) {
      return;
    }
    top.push(addVertex({ x: point.x + lift.x, y: point.y + lift.y, z: point.z + lift.z }));
    bottom.push(
      addVertex({ x: point.x - lift.x, y: point.y - lift.y, z: point.z - lift.z }),
    );
  }
  const fan = [
    [0, 1, 2],
    [0, 2, 3],
    [0, 3, 4],
    [0, 5, 1],
  ];
  for (const [a, b, c] of fan) {
    const t0 = top[a];
    const t1 = top[b];
    const t2 = top[c];
    const b0 = bottom[a];
    const b1 = bottom[b];
    const b2 = bottom[c];
    if (
      t0 === undefined ||
      t1 === undefined ||
      t2 === undefined ||
      b0 === undefined ||
      b1 === undefined ||
      b2 === undefined
    ) {
      continue;
    }
    pushTriangle(t0, t1, t2);
    pushTriangle(b0, b2, b1);
  }
}
