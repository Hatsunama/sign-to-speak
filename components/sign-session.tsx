"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { appendGloss, classifyHand, glossLabel, type RankedGloss } from "@/lib/asl/classify";
import type { Point } from "@/lib/asl/geometry";
import { SignLatch } from "@/lib/asl/latch";
import { MotionWatch } from "@/lib/asl/motion";
import { poseFor, STATIC_SIGNS, type SignName } from "@/lib/asl/poses";
import { createPoseModel, downloadPoseModel, type PoseModel } from "@/lib/pose/instant-hmr";
import { readHandFrame } from "@/lib/pose/read-frame";
import { loadVoices, speakText, stopSpeaking, voicesReady } from "@/lib/speech/speak";
import { KOKORO_VOICES } from "@/lib/speech/voices";

const HandMeshView = dynamic(
  () => import("@/components/hand-mesh-view").then((mod) => mod.HandMeshView),
  { ssr: false },
);

const PRACTICE_COUNT = STATIC_SIGNS.filter(
  (sign) => classifyHand(poseFor(sign))?.id === sign,
).length;

export function SignSession() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const scratchRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<Point[] | null>(null);
  const stageRef = useRef<Point[] | null>(poseFor("ILY"));
  const modelRef = useRef<PoseModel | null>(null);
  const smoothRef = useRef<Point[] | null>(null);
  const latchRef = useRef(new SignLatch());
  const motionRef = useRef(new MotionWatch());
  const busyRef = useRef(false);
  const spokenRef = useRef("");
  const transcriptRef = useRef("");
  const autoRef = useRef(true);
  const voiceRef = useRef("af_heart");
  const speedRef = useRef(1);

  const [mode, setMode] = useState<"camera" | "practice">("practice");
  const [transcript, setTranscript] = useState("");
  const [live, setLive] = useState<RankedGloss | null>(classifyHand(poseFor("ILY")));
  const [poseProgress, setPoseProgress] = useState(0);
  const [poseState, setPoseState] = useState<"idle" | "downloading" | "ready" | "error">("idle");
  const [poseError, setPoseError] = useState<string | null>(null);
  const [voiceProgress, setVoiceProgress] = useState(0);
  const [voiceState, setVoiceState] = useState<"idle" | "downloading" | "ready" | "error">("idle");
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [voiceId, setVoiceId] = useState("af_heart");
  const [speed, setSpeed] = useState(1);
  const [autoSpeak, setAutoSpeak] = useState(true);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [cameraOn, setCameraOn] = useState(false);
  const [speaking, setSpeaking] = useState(false);

  useEffect(() => {
    autoRef.current = autoSpeak;
    voiceRef.current = voiceId;
    speedRef.current = speed;
  }, [autoSpeak, voiceId, speed]);

  const speakNow = useCallback(async (text: string) => {
    setSpeaking(true);
    try {
      await speakText(text, voiceRef.current, speedRef.current);
    } catch (error: unknown) {
      setVoiceError(error instanceof Error ? error.message : "Could not speak");
    } finally {
      setSpeaking(false);
    }
  }, []);

  const maybeSpeak = useCallback(
    (previous: string, next: string) => {
      if (!autoRef.current) {
        return;
      }
      const added = next.slice(previous.length);
      if (!added.includes(" ") && !added.endsWith(" ")) {
        return;
      }
      const pending = next.slice(spokenRef.current.length).trim();
      if (!pending) {
        return;
      }
      spokenRef.current = next;
      void speakNow(pending);
    },
    [speakNow],
  );

  useEffect(() => {
    if (mode !== "camera" || poseState !== "ready") {
      return;
    }
    let frame = 0;
    let stopped = false;
    let lastRead = 0;
    const tick = () => {
      if (stopped) {
        return;
      }
      frame = window.requestAnimationFrame(tick);
      const now = performance.now();
      const video = videoRef.current;
      const scratch = scratchRef.current;
      const model = modelRef.current;
      if (
        !video ||
        !scratch ||
        !model ||
        video.readyState < 2 ||
        busyRef.current ||
        now - lastRead < 140
      ) {
        return;
      }
      lastRead = now;
      busyRef.current = true;
      const box = boxRef.current;
      void readHandFrame({
        video,
        scratch,
        model,
        previous: smoothRef.current,
        motion: motionRef.current,
        box: box ? { width: box.clientWidth, height: box.clientHeight } : null,
        now,
      })
        .then((read) => {
          if (stopped) {
            return;
          }
          if (read.kind === "empty") {
            overlayRef.current = null;
            setLive(null);
            latchRef.current.update(null, now);
            return;
          }
          overlayRef.current = read.overlay;
          stageRef.current = read.stage;
          smoothRef.current = read.smoothed;
          setLive(read.live);
          const committed = latchRef.current.update(read.candidate, now);
          if (!committed) {
            return;
          }
          const previous = transcriptRef.current;
          const next = appendGloss(previous, committed);
          transcriptRef.current = next;
          setTranscript(next);
          maybeSpeak(previous, next);
        })
        .catch((error: unknown) => {
          setPoseError(error instanceof Error ? error.message : "The hand model stopped");
        })
        .finally(() => {
          busyRef.current = false;
        });
    };
    frame = window.requestAnimationFrame(tick);
    return () => {
      stopped = true;
      window.cancelAnimationFrame(frame);
    };
  }, [mode, poseState, maybeSpeak]);

  useEffect(() => {
    const video = videoRef.current;
    return () => {
      const stream = video?.srcObject;
      if (stream instanceof MediaStream) {
        for (const track of stream.getTracks()) {
          track.stop();
        }
      }
    };
  }, []);

  async function downloadPose() {
    setPoseState("downloading");
    setPoseError(null);
    try {
      const buffer = await downloadPoseModel(setPoseProgress);
      modelRef.current = await createPoseModel(buffer);
      setPoseState("ready");
      await startCamera();
    } catch (error: unknown) {
      setPoseState("error");
      setPoseError(error instanceof Error ? error.message : "Could not load the hand model");
    }
  }

  async function startCamera() {
    setCameraError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      const video = videoRef.current;
      if (!video) {
        return;
      }
      video.srcObject = stream;
      await video.play();
      setCameraOn(true);
      setMode("camera");
      latchRef.current.reset();
      motionRef.current.reset();
      smoothRef.current = null;
    } catch (error: unknown) {
      setCameraError(
        error instanceof Error ? error.message : "Camera permission is required",
      );
    }
  }

  async function downloadVoices() {
    setVoiceState("downloading");
    setVoiceError(null);
    try {
      await loadVoices((ratio) => setVoiceProgress(ratio));
      setVoiceState("ready");
    } catch (error: unknown) {
      setVoiceState("error");
      setVoiceError(error instanceof Error ? error.message : "Could not download voices");
    }
  }

  function posePractice(sign: SignName) {
    const joints = poseFor(sign);
    stageRef.current = joints;
    overlayRef.current = null;
    setLive(classifyHand(joints));
    setMode("practice");
  }

  function writeTranscript(next: string) {
    transcriptRef.current = next;
    setTranscript(next);
  }

  function addLive() {
    if (!live) {
      return;
    }
    writeTranscript(appendGloss(transcriptRef.current, live.id));
  }

  const poseLabel = live ? glossLabel(live.id) : "—";

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-4 py-4 md:px-6 md:py-6">
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-heading text-3xl tracking-tight text-[#f6efe4]">Sign To Speak</p>
          <Badge variant="secondary">Hands only</Badge>
        </div>
        <p className="max-w-2xl text-sm leading-6 text-[#cbbba6]">
          The camera recovers a 3D hand the way InstantHMR recovers a body: one crop,
          then joints, then a mesh. Only the signing hand is drawn. Hold a fingerspelled
          letter until it locks, and the phone speaks it.
        </p>
      </header>

      <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(300px,0.85fr)]">
        <section className="flex min-w-0 flex-col gap-3">
          <div className="flex gap-2">
            <Button
              variant={mode === "practice" ? "default" : "outline"}
              className="h-11"
              onClick={() => setMode("practice")}
            >
              Practice shapes
            </Button>
            <Button
              variant={mode === "camera" ? "default" : "outline"}
              className="h-11"
              onClick={() => setMode("camera")}
            >
              Camera
            </Button>
          </div>

          <div
            ref={boxRef}
            className="relative aspect-[3/4] overflow-hidden rounded-3xl bg-[#1a1612] ring-1 ring-white/10 sm:aspect-video"
          >
            <video
              ref={videoRef}
              className={`h-full w-full -scale-x-100 object-cover ${mode === "camera" && cameraOn ? "block" : "hidden"}`}
              playsInline
              muted
              autoPlay
            />
            {mode === "camera" && cameraOn ? (
              <div className="pointer-events-none absolute inset-0 -scale-x-100">
                <HandMeshView jointsRef={overlayRef} mode="overlay" />
              </div>
            ) : (
              <HandMeshView jointsRef={stageRef} mode="stage" />
            )}
            {mode === "camera" && cameraOn ? (
              <div className="absolute right-3 bottom-3 h-36 w-28 overflow-hidden rounded-2xl bg-[#14110e]/80 ring-1 ring-white/15">
                <HandMeshView jointsRef={stageRef} mode="stage" />
              </div>
            ) : null}
            <div className="absolute top-3 left-3 rounded-full bg-black/50 px-3 py-1 text-xs text-[#f6efe4]">
              {mode === "camera" ? (cameraOn ? "Live hand mesh" : "Camera off") : "Practice mesh"}
            </div>
            <p className="pointer-events-none absolute bottom-3 left-4 font-heading text-6xl text-[#f6efe4] drop-shadow">
              {poseLabel}
            </p>
          </div>
          <canvas ref={scratchRef} className="hidden" />

          {poseState !== "ready" || cameraError || (mode === "camera" && !cameraOn) ? (
            <Card>
              <CardHeader>
                <CardTitle>Download the hand model</CardTitle>
                <CardDescription>
                  InstantHMR, about 81 MB from Hugging Face, stays on this phone. The full
                  body is estimated so the fingers can be found, and only the hand mesh is shown.
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-3">
                <Button className="h-12" onClick={() => void downloadPose()} disabled={poseState === "downloading"}>
                  {poseState === "downloading"
                    ? `Downloading ${Math.round(poseProgress * 100)}%`
                    : poseState === "ready"
                      ? "Start camera"
                      : "Download hand model"}
                </Button>
                {poseState === "ready" ? (
                  <Button variant="outline" className="h-12" onClick={() => void startCamera()}>
                    Open camera
                  </Button>
                ) : null}
                {poseError ? <p className="text-sm text-destructive">{poseError}</p> : null}
                {cameraError ? <p className="text-sm text-destructive">{cameraError}</p> : null}
              </CardContent>
            </Card>
          ) : null}

          <div className="flex w-full min-w-0 gap-2 overflow-x-auto pb-1">
            {STATIC_SIGNS.map((sign) => (
              <Button
                key={sign}
                variant="outline"
                className="h-11 shrink-0 px-3"
                onClick={() => posePractice(sign)}
              >
                {glossLabel(sign)}
              </Button>
            ))}
          </div>
          <p className="text-xs text-[#a89886]">
            {PRACTICE_COUNT} of {STATIC_SIGNS.length} practice shapes match the reader. J and Z
            are strokes, not still shapes. Open palm is a space. Thumbs up is yes, thumbs down
            is no.
          </p>
        </section>

        <section className="flex min-w-0 flex-col gap-3">
          <Card>
            <CardHeader>
              <CardTitle>Line</CardTitle>
              <CardDescription>
                {live
                  ? `${glossLabel(live.id)} · ${Math.round(live.score * 100)}%`
                  : "Waiting for a hand"}
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <p className="min-h-24 rounded-2xl bg-[#2a241e] px-4 py-3 font-heading text-3xl leading-snug text-[#f6efe4]">
                {transcript || "Letters will gather here."}
              </p>
              <div className="grid grid-cols-2 gap-2">
                <Button className="h-12" onClick={() => void speakNow(transcript)} disabled={!transcript.trim() || speaking}>
                  {speaking ? "Speaking" : "Speak"}
                </Button>
                <Button variant="outline" className="h-12" onClick={() => stopSpeaking()}>
                  Stop
                </Button>
                <Button variant="secondary" className="h-12" onClick={addLive} disabled={!live}>
                  Add shape
                </Button>
                <Button
                  variant="secondary"
                  className="h-12"
                  onClick={() => writeTranscript(appendGloss(transcriptRef.current, "SPACE"))}
                >
                  Space
                </Button>
                <Button
                  variant="outline"
                  className="h-12"
                  onClick={() => writeTranscript(transcriptRef.current.slice(0, -1))}
                >
                  Delete
                </Button>
                <Button
                  variant="outline"
                  className="h-12"
                  onClick={() => {
                    writeTranscript("");
                    spokenRef.current = "";
                  }}
                >
                  Clear
                </Button>
              </div>
              <label className="flex items-center justify-between gap-3 text-sm">
                Speak each word as it locks
                <Switch checked={autoSpeak} onCheckedChange={setAutoSpeak} />
              </label>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Voices</CardTitle>
              <CardDescription>
                Kokoro, downloaded from Hugging Face after you ask. Several speakers, not the
                single device voice.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {voiceState !== "ready" ? (
                <Button className="h-12" onClick={() => void downloadVoices()} disabled={voiceState === "downloading"}>
                  {voiceState === "downloading"
                    ? `Downloading voices ${Math.round(voiceProgress * 100)}%`
                    : "Download voices"}
                </Button>
              ) : (
                <p className="text-sm text-[#cbbba6]">Voices are on this phone.</p>
              )}
              {voiceError ? <p className="text-sm text-destructive">{voiceError}</p> : null}
              <div className="grid grid-cols-2 gap-2">
                {KOKORO_VOICES.map((voice) => (
                  <Button
                    key={voice.id}
                    variant={voiceId === voice.id ? "default" : "outline"}
                    className="h-auto flex-col items-start py-2"
                    onClick={() => setVoiceId(voice.id)}
                  >
                    <span>{voice.name}</span>
                    <span className="text-xs font-normal opacity-70">{voice.detail}</span>
                  </Button>
                ))}
              </div>
              <div className="flex flex-col gap-2">
                <div className="flex justify-between text-xs text-[#cbbba6]">
                  <span>Speed</span>
                  <span>{speed.toFixed(2)}</span>
                </div>
                <Slider
                  min={0.8}
                  max={1.25}
                  step={0.05}
                  value={[speed]}
                  onValueChange={(value) => {
                    const next = Array.isArray(value) ? value[0] : value;
                    if (typeof next === "number") {
                      setSpeed(next);
                    }
                  }}
                />
              </div>
              <Button
                variant="outline"
                className="h-11"
                onClick={() =>
                  void speakNow(
                    voicesReady()
                      ? `This is ${KOKORO_VOICES.find((voice) => voice.id === voiceId)?.name ?? "the voice"}. I will read what you sign.`
                      : "Download the voices to leave the built-in phone voice.",
                  )
                }
              >
                Preview voice
              </Button>
            </CardContent>
          </Card>
        </section>
      </div>
    </div>
  );
}


