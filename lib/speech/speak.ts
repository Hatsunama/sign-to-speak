import { VOICE_MODEL } from "./voices";

type SpeechProgress = {
  status?: string;
  file?: string;
  loaded?: number;
  total?: number;
};

type KokoroEngine = {
  generate: (
    text: string,
    options: { voice: string; speed?: number },
  ) => Promise<{ toBlob: () => Blob }>;
};

let engine: KokoroEngine | null = null;
let playing: HTMLAudioElement | null = null;

export function voicesReady(): boolean {
  return engine !== null;
}

export async function loadVoices(
  onProgress: (ratio: number, label: string) => void,
): Promise<void> {
  const { KokoroTTS } = await import("kokoro-js");
  const loaded = await KokoroTTS.from_pretrained(VOICE_MODEL, {
    dtype: "q8",
    device: "wasm",
    progress_callback: (info: SpeechProgress) => {
      if (info.status === "progress" && info.total) {
        onProgress((info.loaded ?? 0) / info.total, info.file ?? "voices");
      }
    },
  });
  engine = loaded as unknown as KokoroEngine;
  onProgress(1, "ready");
}

export async function speakText(text: string, voice: string, speed: number): Promise<void> {
  const cleaned = text.trim();
  if (!cleaned) {
    return;
  }
  stopSpeaking();
  if (!engine) {
    speakWithDevice(cleaned, speed);
    return;
  }
  const audio = await engine.generate(cleaned, { voice, speed });
  const url = URL.createObjectURL(audio.toBlob());
  const element = new Audio(url);
  playing = element;
  element.onended = () => {
    URL.revokeObjectURL(url);
    if (playing === element) {
      playing = null;
    }
  };
  await element.play();
}

export function stopSpeaking(): void {
  playing?.pause();
  playing = null;
  if (typeof window !== "undefined" && window.speechSynthesis) {
    window.speechSynthesis.cancel();
  }
}

function speakWithDevice(text: string, speed: number): void {
  if (typeof window === "undefined" || !window.speechSynthesis) {
    return;
  }
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = Math.min(1.4, Math.max(0.7, speed));
  window.speechSynthesis.speak(utterance);
}
