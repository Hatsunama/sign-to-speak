# Signspeaker

Signspeaker watches one hand through the phone camera, draws a 3D mesh on that hand, and speaks the fingerspelled line in a voice you pick.

## What a 1.5B Qwen model can and cannot do

It cannot reliably understand ASL, or any other sign language. Caption Studio’s Qwen2.5-1.5B is a text model. It rewrites subtitle lines (`qwen2.5-caption-json-v2`) and ships on Android as a LiteRT-LM file of about 1.6 GB. It never sees a camera frame. A text model has no handshape, movement, or face channel, so “run Qwen 1.5B on the phone and it will translate signing” does not hold.

Open conversation in ASL is still a research problem. A 2B vision model fine-tuned for ASL can sound fluent and still miss the words. This app does not claim that.

## How the hand is mapped

[InstantHMR](https://huggingface.co/momolesang/InstantHMR) (the model in Mohamed Adjel’s phone demo) recovers a 70-joint body pose from one 224×224 crop, including a full finger chain on each hand: wrist, three joints, and the tip. On a Galaxy S23 the native build is about 10 ms after the detector. This app uses that same ONNX file, downloaded from Hugging Face after install (about 81 MB).

The body is not drawn. The signing hand is lifted out of those 70 joints, skinned into a mesh, and read as:

- the ASL fingerspelling alphabet
- J and Z as strokes
- a few whole signs: open palm (space), thumbs up (yes), thumbs down (no), I love you

Hold a shape until it locks. Drop the hand before repeating a letter. The mesh is the check: if the fingers look wrong on screen, the letter will be wrong too. InstantHMR was trained as a body model, and finger detail is the part its authors are still tightening, so this is a faithful hand mapping of that pipeline, not a certified interpreter.

## Voices

Speech is [Kokoro-82M](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX), also downloaded from Hugging Face when you ask. Heart, Bella, Nicole, Sarah, Michael, Fenrir, Puck, Emma, Isabella, George, and Fable. Until that download finishes, preview falls back to the phone’s own voice.

## Run it

```bash
npm install
npm run dev
```

The dev server listens on port 43123. On an Android phone, open that URL in Chrome on the same network, allow the camera, then Add to Home screen.

```bash
npm test
npm run typecheck
```

The pose weights are under the SAM license because InstantHMR is distilled from `facebook/sam-3d-body-dinov3`. This app downloads them at runtime and does not redistribute the file.
