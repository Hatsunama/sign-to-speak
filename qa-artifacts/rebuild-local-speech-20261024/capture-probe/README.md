# Playback capture privacy QA probes

Two local, Java-only, test-only APKs are built by `python verification/java-check/capture-qa/build.py`. The build uses Android API 36, build-tools 36.0.0, and JDK 17 with a 128 MiB Java heap limit. The `out/*-unsigned.apk` artifacts are intentionally unsigned. `out/receipt.txt` records source and APK SHA-256 plus DEX CRC32; `zipalign -c` and ZIP CRC pass.

The recorder requires normal `RECORD_AUDIO` permission and a fresh user-approved MediaProjection consent. It starts a `mediaProjection` foreground service before obtaining the projection. It creates no virtual display or screen pixel consumer. Its `AudioRecord` accepts only `USAGE_MEDIA` from the exact installed UIDs of `com.xmilo_at_your_side.sign_to_speak.qa` and `com.xmilo_at_your_side.sign_to_speak.qa.tone`. It reads mono 16 kHz PCM16 in a small reusable array, counts samples/nonzero samples/peak, then wipes the array. No PCM or recognized speech is persisted or logged. Each phase lasts at most 20 seconds and the whole consent session at most 60 seconds. The callback stops on revocation. If the product or tone package is absent, the phase fails with a log reason.

The tone app declares `allowAudioPlaybackCapture=true`, sets both manager and track policy to `ALLOW_CAPTURE_BY_ALL`, and uses `USAGE_MEDIA`/`CONTENT_TYPE_MUSIC`. Its Play button writes an at-most-five-second 440 Hz sine wave. It logs only written and playback-head frame counts and stops on pause/destroy. No background playback or automatic resume.

## On each phone

Use the already approved development signing workflow to sign both unsigned APKs. No key or password is part of this probe. Replace `<serial>`, `<recorder-signed.apk>`, and `<tone-signed.apk>` below. The production QA app must already be installed under `com.xmilo_at_your_side.sign_to_speak.qa` in the same Android user profile.

```powershell
adb -s <serial> install -t -r <recorder-signed.apk>
adb -s <serial> install -t -r <tone-signed.apk>
$logStart = (adb -s <serial> shell "date '+%m-%d %H:%M:%S.000'").Trim()
adb -s <serial> shell am start -n com.xmilo_at_your_side.sign_to_speak.qa.capture/.RecorderActivity
```

The phone-derived timestamp bounds the later tag-filtered log collection to this run without clearing other apps' logs. Tap **Grant capture consent**, allow `RECORD_AUDIO`, and approve the OS projection prompt. Select **entire screen** if the OS offers an app-window choice. The screen consent is required even though the probe reads audio only. While the notification is active, arm the control phase; open the tone app and tap Play. The phase command is restricted by `android.permission.DUMP` on the exported receiver and is intended for adb shell only.

```powershell
adb -s <serial> shell am broadcast --receiver-foreground -n com.xmilo_at_your_side.sign_to_speak.qa.capture/.PhaseReceiver -a com.xmilo_at_your_side.sign_to_speak.qa.capture.PHASE --es phase CONTROL
adb -s <serial> shell am start -n com.xmilo_at_your_side.sign_to_speak.qa.tone/.ToneActivity
```

Return to the product QA app after the control phase ends. Arm PROTECTED and trigger actual speech while the phase is active. The parent QA workflow must collect independent product playback-head progress evidence. Wait for each phase to end before evaluating its counts. Finish before the 60 second session timeout; otherwise obtain fresh consent and run the protected phase in a new session.

```powershell
adb -s <serial> shell am broadcast --receiver-foreground -n com.xmilo_at_your_side.sign_to_speak.qa.capture/.PhaseReceiver -a com.xmilo_at_your_side.sign_to_speak.qa.capture.PHASE --es phase PROTECTED
adb -s <serial> logcat -d -T $logStart -s CaptureQA:I ToneQA:I '*:S'
adb -s <serial> shell am broadcast --receiver-foreground -n com.xmilo_at_your_side.sign_to_speak.qa.capture/.PhaseReceiver -a com.xmilo_at_your_side.sign_to_speak.qa.capture.PHASE --es phase STOP
```

Accept privacy only when the same phone shows a successful control phase with `samples>0`, `nonzero>0`, `readErrors=0`, no cleanup failure, and tone `headFrames>0`; then a protected phase with `samples>0`, `nonzero=0`, `peak=0`, `readErrors=0` and no cleanup failure while the product's real speech playback head progresses. A denial, revoked projection, timeout before stimulus, missing UID, failed recorder start/read, silent control, or absent product head progress is inconclusive or a failure, never a privacy pass. Repeat the sequence on both Android 16 phones. The probe does not change production manifest, `AudioManager`, or `AudioTrack` capture policy.
