import {
  classifyHand,
  rankHand,
  type GlossId,
  type RankedGloss,
} from "@/lib/asl/classify";
import { jointToFrame, type FrameCrop } from "@/lib/asl/crop";
import { dist, type Point } from "@/lib/asl/geometry";
import { handFromMhr, mhrJointsToPoints, selectSigningHand } from "@/lib/asl/mhr";
import type { MotionWatch } from "@/lib/asl/motion";
import type { PoseModel } from "@/lib/pose/instant-hmr";
import { imageTensorFromFrame } from "@/lib/pose/instant-hmr";

export type FrameRead =
  | { kind: "empty" }
  | {
      kind: "hand";
      overlay: Point[] | null;
      stage: Point[];
      smoothed: Point[];
      live: RankedGloss | null;
      candidate: GlossId | null;
    };

export async function readHandFrame(input: {
  video: HTMLVideoElement;
  scratch: HTMLCanvasElement;
  model: PoseModel;
  previous: Point[] | null;
  motion: MotionWatch;
  box: { width: number; height: number } | null;
  now: number;
}): Promise<FrameRead> {
  const feeds = imageTensorFromFrame(input.video, input.scratch, input.model.Tensor);
  const outputs = await input.model.session.run({
    image: feeds.image,
    cliff_cond: feeds.cliff,
  });
  const joints3 = outputs.joints_3d;
  const joints2 = outputs.joints_2d;
  if (!joints3 || !joints2) {
    return { kind: "empty" };
  }
  const points3 = mhrJointsToPoints(joints3.data, "xyz");
  const points2 = mhrJointsToPoints(joints2.data, "xy");
  const chosen = selectSigningHand(points3);
  if (!chosen) {
    return { kind: "empty" };
  }
  const smoothed = smoothInto(input.previous, chosen.hand);
  const flat = handFromMhr(points2, chosen.side);
  const overlay =
    flat && input.box
      ? projectHand(
          flat,
          smoothed,
          feeds.crop,
          input.video,
          input.box.width,
          input.box.height,
        )
      : null;
  const ranked = rankHand(smoothed)[0] ?? null;
  const stable = classifyHand(smoothed);
  const motion = input.motion.push(smoothed, stable?.id ?? null, input.now);
  const candidate = motion.moving && !motion.gloss ? null : (motion.gloss ?? stable?.id ?? null);
  return {
    kind: "hand",
    overlay,
    stage: toStage(smoothed),
    smoothed,
    live: motion.gloss ? { id: motion.gloss, score: 1 } : ranked,
    candidate,
  };
}

function smoothInto(previous: Point[] | null, next: Point[]): Point[] {
  if (!previous || previous.length !== next.length) {
    return next;
  }
  return next.map((point, index) => {
    const prior = previous[index];
    if (!prior) {
      return point;
    }
    return {
      x: prior.x * 0.45 + point.x * 0.55,
      y: prior.y * 0.45 + point.y * 0.55,
      z: prior.z * 0.45 + point.z * 0.55,
    };
  });
}

function toStage(hand: Point[]): Point[] {
  const wrist = hand[0];
  if (!wrist) {
    return hand;
  }
  return hand.map((point) => ({
    x: point.x - wrist.x,
    y: -(point.y - wrist.y),
    z: point.z - wrist.z,
  }));
}

function projectHand(
  flat: Point[],
  depth: Point[],
  crop: FrameCrop,
  video: HTMLVideoElement,
  elementWidth: number,
  elementHeight: number,
): Point[] {
  const projected = flat.map((point) => jointToFrame(point.x, point.y, crop));
  const wrist = depth[0];
  const middle = depth[9];
  const palmMeters = wrist && middle ? Math.max(dist(wrist, middle), 1e-4) : 1;
  const origin = projected[0];
  const middlePx = projected[9];
  const palmPixels =
    origin && middlePx ? Math.max(Math.hypot(middlePx.x - origin.x, middlePx.y - origin.y), 1) : 1;
  const depthScale = palmPixels / palmMeters;
  return projected.map((point, index) => {
    const placed = placeOnElement(point.x, point.y, video, elementWidth, elementHeight);
    const sample = depth[index];
    const z = wrist && sample ? (sample.z - wrist.z) * depthScale : 0;
    return { x: placed.x, y: placed.y, z };
  });
}

function placeOnElement(
  px: number,
  py: number,
  video: HTMLVideoElement,
  elementWidth: number,
  elementHeight: number,
): { x: number; y: number } {
  const videoWidth = video.videoWidth || 1;
  const videoHeight = video.videoHeight || 1;
  const videoAspect = videoWidth / videoHeight;
  const elementAspect = elementWidth / Math.max(elementHeight, 1);
  let scale = 1;
  let offsetX = 0;
  let offsetY = 0;
  if (elementAspect > videoAspect) {
    scale = elementWidth / videoWidth;
    offsetY = (elementHeight - videoHeight * scale) / 2;
  } else {
    scale = elementHeight / videoHeight;
    offsetX = (elementWidth - videoWidth * scale) / 2;
  }
  return { x: offsetX + px * scale, y: offsetY + py * scale };
}
