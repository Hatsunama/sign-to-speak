import { dist, sub, type Point } from "./geometry";
import type { GlossId } from "./classify";

type Sample = {
  t: number;
  index: Point;
  pinky: Point;
  label: GlossId | null;
};

export class MotionWatch {
  private samples: Sample[] = [];

  reset(): void {
    this.samples = [];
  }

  push(
    landmarks: Point[],
    label: GlossId | null,
    now: number,
  ): { gloss: GlossId | null; moving: boolean } {
    const index = landmarks[8];
    const pinky = landmarks[20];
    const wrist = landmarks[0];
    const middle = landmarks[9];
    if (!index || !pinky || !wrist || !middle) {
      return { gloss: null, moving: false };
    }
    this.samples.push({ t: now, index, pinky, label });
    const cutoff = now - 900;
    this.samples = this.samples.filter((sample) => sample.t >= cutoff);
    const palm = Math.max(dist(wrist, middle), 1e-4);
    const indexPath = pathStats(this.samples.map((sample) => sample.index));
    const pinkyPath = pathStats(this.samples.map((sample) => sample.pinky));
    const moving = indexPath.length > palm * 0.35 || pinkyPath.length > palm * 0.35;
    const mostly = (id: GlossId) => {
      const relevant = this.samples.filter((sample) => sample.label === id).length;
      return relevant >= Math.max(3, this.samples.length * 0.5);
    };
    if (mostly("I") && pinkyPath.length > palm * 0.22 && pinkyPath.turns >= 1) {
      return { gloss: "J", moving: false };
    }
    if (
      (mostly("D") || mostly("G") || mostly("X")) &&
      indexPath.length > palm * 0.55 &&
      indexPath.turns >= 2
    ) {
      return { gloss: "Z", moving: false };
    }
    return { gloss: null, moving };
  }
}

function pathStats(points: Point[]): { length: number; turns: number } {
  let length = 0;
  let turns = 0;
  let previous: Point | null = null;
  for (let index = 1; index < points.length; index += 1) {
    const from = points[index - 1];
    const to = points[index];
    if (!from || !to) {
      continue;
    }
    const delta = sub(to, from);
    const step = Math.hypot(delta.x, delta.y, delta.z);
    if (step < 0.003) {
      continue;
    }
    length += step;
    const direction = { x: delta.x / step, y: delta.y / step, z: delta.z / step };
    if (previous) {
      const alignment = Math.min(
        1,
        Math.max(
          -1,
          previous.x * direction.x + previous.y * direction.y + previous.z * direction.z,
        ),
      );
      if (alignment < 0.45) {
        turns += 1;
      }
    }
    previous = direction;
  }
  return { length, turns };
}
