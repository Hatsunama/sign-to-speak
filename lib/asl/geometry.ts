export type Point = {
  x: number;
  y: number;
  z: number;
};

export function sub(a: Point, b: Point): Point {
  return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z };
}

export function add(a: Point, b: Point): Point {
  return { x: a.x + b.x, y: a.y + b.y, z: a.z + b.z };
}

export function scale(a: Point, s: number): Point {
  return { x: a.x * s, y: a.y * s, z: a.z * s };
}

export function dot(a: Point, b: Point): number {
  return a.x * b.x + a.y * b.y + a.z * b.z;
}

export function cross(a: Point, b: Point): Point {
  return {
    x: a.y * b.z - a.z * b.y,
    y: a.z * b.x - a.x * b.z,
    z: a.x * b.y - a.y * b.x,
  };
}

export function mag(a: Point): number {
  return Math.hypot(a.x, a.y, a.z);
}

export function norm(a: Point): Point {
  const length = mag(a);
  if (length < 1e-8) {
    return { x: 0, y: 0, z: 0 };
  }
  return scale(a, 1 / length);
}

export function dist(a: Point, b: Point): number {
  return mag(sub(a, b));
}

export function clamp(value: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, value));
}

export function lerp(a: Point, b: Point, t: number): Point {
  return add(a, scale(sub(b, a), t));
}

/** Interior angle at `b`, in degrees. Straight is about 180. */
export function angleAt(a: Point, b: Point, c: Point): number {
  const left = sub(a, b);
  const right = sub(c, b);
  const denom = mag(left) * mag(right);
  if (denom < 1e-8) {
    return 0;
  }
  const cosine = clamp(dot(left, right) / denom, -1, 1);
  return (Math.acos(cosine) * 180) / Math.PI;
}

export function rotateZ(point: Point, degrees: number): Point {
  const radians = (degrees * Math.PI) / 180;
  const cos = Math.cos(radians);
  const sin = Math.sin(radians);
  return {
    x: point.x * cos - point.y * sin,
    y: point.x * sin + point.y * cos,
    z: point.z,
  };
}

export function jitter(point: Point, amount: number, seed: number): Point {
  const n = (shift: number) => {
    const raw = Math.sin(seed * 12.9898 + shift * 78.233) * 43758.5453;
    return raw - Math.floor(raw) - 0.5;
  };
  return {
    x: point.x + n(1) * amount,
    y: point.y + n(2) * amount,
    z: point.z + n(3) * amount,
  };
}
