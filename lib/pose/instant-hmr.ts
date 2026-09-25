import { frameCrop, type FrameCrop } from "@/lib/asl/crop";

const MODEL_URL =
  "https://huggingface.co/momolesang/InstantHMR/resolve/main/instanthmr.onnx";

const MEAN = [0.485, 0.456, 0.406];
const STD = [0.229, 0.224, 0.225];

type OrtModule = typeof import("onnxruntime-web/wasm");

export type PoseModel = {
  session: Awaited<ReturnType<OrtModule["InferenceSession"]["create"]>>;
  Tensor: OrtModule["Tensor"];
};

export async function downloadPoseModel(
  onProgress: (ratio: number) => void,
): Promise<ArrayBuffer> {
  if (typeof caches === "undefined") {
    return fetchBuffer(MODEL_URL, onProgress);
  }
  const cache = await caches.open("sign-to-speak-pose-v1");
  const cached = await cache.match(MODEL_URL);
  if (cached) {
    onProgress(1);
    return cached.arrayBuffer();
  }
  const buffer = await fetchBuffer(MODEL_URL, onProgress);
  await cache.put(
    MODEL_URL,
    new Response(buffer.slice(0), {
      headers: { "content-type": "application/octet-stream" },
    }),
  );
  return buffer;
}

export async function createPoseModel(buffer: ArrayBuffer): Promise<PoseModel> {
  const ort = await import("onnxruntime-web/wasm");
  ort.env.wasm.numThreads = 1;
  const session = await ort.InferenceSession.create(buffer, {
    executionProviders: ["wasm"],
    graphOptimizationLevel: "all",
  });
  return { session, Tensor: ort.Tensor };
}

export function imageTensorFromFrame(
  video: HTMLVideoElement,
  scratch: HTMLCanvasElement,
  Tensor: PoseModel["Tensor"],
): { image: unknown; cliff: unknown; crop: FrameCrop } {
  const width = video.videoWidth;
  const height = video.videoHeight;
  const crop = frameCrop(width, height);
  const context = scratch.getContext("2d", { willReadFrequently: true });
  if (!context) {
    throw new Error("Could not read the camera frame");
  }
  scratch.width = 224;
  scratch.height = 224;
  context.fillStyle = "#000";
  context.fillRect(0, 0, 224, 224);
  const scale = 224 / crop.size;
  context.drawImage(video, -crop.sx1 * scale, -crop.sy1 * scale, width * scale, height * scale);
  const pixels = context.getImageData(0, 0, 224, 224).data;
  const data = new Float32Array(3 * 224 * 224);
  const plane = 224 * 224;
  for (let index = 0; index < plane; index += 1) {
    const offset = index * 4;
    for (let channel = 0; channel < 3; channel += 1) {
      const value = (pixels[offset + channel] ?? 0) / 255;
      const mean = MEAN[channel] ?? 0;
      const std = STD[channel] ?? 1;
      data[channel * plane + index] = (value - mean) / std;
    }
  }
  return {
    image: new Tensor("float32", data, [1, 3, 224, 224]),
    cliff: new Tensor("float32", Float32Array.from(crop.cliff), [1, 3]),
    crop,
  };
}

async function fetchBuffer(
  url: string,
  onProgress: (ratio: number) => void,
): Promise<ArrayBuffer> {
  const response = await fetch(url);
  if (!response.ok || !response.body) {
    throw new Error("Could not download the hand model from Hugging Face");
  }
  const total = Number(response.headers.get("content-length") ?? 0);
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let loaded = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    if (!value) {
      continue;
    }
    chunks.push(value);
    loaded += value.byteLength;
    if (total > 0) {
      onProgress(Math.min(0.99, loaded / total));
    }
  }
  const buffer = new Uint8Array(loaded);
  let offset = 0;
  for (const chunk of chunks) {
    buffer.set(chunk, offset);
    offset += chunk.byteLength;
  }
  onProgress(1);
  return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
}
