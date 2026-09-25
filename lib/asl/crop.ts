export type FrameCrop = {
  cx: number;
  cy: number;
  size: number;
  sx1: number;
  sy1: number;
  cliff: [number, number, number];
};

export type Box = {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
};

/** Square 1.2× crop used by InstantHMR, plus the CLIFF camera vector. */
export function frameCrop(
  frameWidth: number,
  frameHeight: number,
  box?: Box,
): FrameCrop {
  const source = box ?? { x1: 0, y1: 0, x2: frameWidth, y2: frameHeight };
  const cx = (source.x1 + source.x2) / 2;
  const cy = (source.y1 + source.y2) / 2;
  const boxWidth = source.x2 - source.x1;
  const boxHeight = source.y2 - source.y1;
  const size = Math.max(boxWidth, boxHeight) * 1.2;
  return {
    cx,
    cy,
    size,
    sx1: cx - size / 2,
    sy1: cy - size / 2,
    cliff: [
      (2 * cx) / frameWidth - 1,
      (2 * cy) / frameHeight - 1,
      Math.max(boxWidth, boxHeight) / Math.max(frameWidth, frameHeight),
    ],
  };
}

/** Map a joint from crop-normalized [-1, 1] back into frame pixels. */
export function jointToFrame(
  nx: number,
  ny: number,
  crop: FrameCrop,
): { x: number; y: number } {
  return {
    x: (nx * 0.5 + 0.5) * crop.size + crop.sx1,
    y: (ny * 0.5 + 0.5) * crop.size + crop.sy1,
  };
}
