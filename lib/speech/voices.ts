export type VoiceChoice = {
  id: string;
  name: string;
  detail: string;
};

export const KOKORO_VOICES: VoiceChoice[] = [
  { id: "af_heart", name: "Heart", detail: "American woman" },
  { id: "af_bella", name: "Bella", detail: "American woman" },
  { id: "af_nicole", name: "Nicole", detail: "American woman" },
  { id: "af_sarah", name: "Sarah", detail: "American woman" },
  { id: "am_michael", name: "Michael", detail: "American man" },
  { id: "am_fenrir", name: "Fenrir", detail: "American man" },
  { id: "am_puck", name: "Puck", detail: "American man" },
  { id: "bf_emma", name: "Emma", detail: "British woman" },
  { id: "bf_isabella", name: "Isabella", detail: "British woman" },
  { id: "bm_george", name: "George", detail: "British man" },
  { id: "bm_fable", name: "Fable", detail: "British man" },
];

export const VOICE_MODEL = "onnx-community/Kokoro-82M-v1.0-ONNX";
