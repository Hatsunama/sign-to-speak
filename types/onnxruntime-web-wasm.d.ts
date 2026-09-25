declare module "onnxruntime-web/wasm" {
  export const env: {
    wasm: {
      numThreads: number;
    };
  };

  export class Tensor {
    constructor(type: "float32", data: Float32Array, dims: number[]);
  }

  export class InferenceSession {
    static create(
      buffer: ArrayBuffer,
      options: { executionProviders: string[]; graphOptimizationLevel: string },
    ): Promise<{
      run: (feeds: Record<string, unknown>) => Promise<
        Record<string, { data: Float32Array; dims: number[] }>
      >;
    }>;
  }
}
