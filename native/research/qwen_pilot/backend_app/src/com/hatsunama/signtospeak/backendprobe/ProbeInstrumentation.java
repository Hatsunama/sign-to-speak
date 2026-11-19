// SPDX-License-Identifier: Apache-2.0
package com.hatsunama.signtospeak.backendprobe;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.os.Build;
import android.os.Bundle;
import android.os.Process;

import org.json.JSONObject;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;

public final class ProbeInstrumentation extends Instrumentation {
    private ProbeContract.Request request;

    @Override
    public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        try {
            if (arguments == null || arguments.size() != 3) {
                throw new IllegalArgumentException("Exactly runid, backend, and case are required");
            }
            request = ProbeContract.parse(
                    arguments.getString("runid"),
                    arguments.getString("backend"),
                    arguments.getString("case"));
        } catch (RuntimeException error) {
            finishWithStatus(Activity.RESULT_CANCELED, "Invalid arguments: " + error.getMessage());
            return;
        }
        start();
    }

    @Override
    public void onStart() {
        try {
            Context context = getTargetContext();
            Path runDir = request.createRunDirectory(context.getFilesDir().toPath());
            // Publish the start receipt before loading the library or invoking upstream code.
            JSONObject receipt = new JSONObject()
                    .put("phase", "started")
                    .put("runId", request.runId)
                    .put("pid", Process.myPid())
                    .put("androidApi", Build.VERSION.SDK_INT)
                    .put("targetSdk", context.getApplicationInfo().targetSdkVersion)
                    .put("backend", request.backend)
                    .put("case", request.testCase)
                    .put("startedAtMillis", System.currentTimeMillis())
                    .put("endedAtMillis", JSONObject.NULL)
                    .put("nativeExitCode", JSONObject.NULL)
                    .put("nativeFailure", JSONObject.NULL);
            writeReceipt(runDir.resolve("run.json"), receipt);

            Integer exitCode = null;
            String nativeFailure = null;
            try {
                System.loadLibrary("sign_backend_probe");
                exitCode = runNative(runDir.toString(), request.backend, request.testCase);
                if (exitCode != 0) {
                    nativeFailure = "Native backend probe returned exit code " + exitCode;
                }
            } catch (LinkageError | RuntimeException error) {
                nativeFailure = error.toString();
            }

            receipt.put("phase", "completed")
                    .put("endedAtMillis", System.currentTimeMillis())
                    .put("nativeExitCode", exitCode == null ? JSONObject.NULL : exitCode)
                    .put("nativeFailure", nativeFailure == null ? JSONObject.NULL : nativeFailure);
            writeReceipt(runDir.resolve("result.json"), receipt);

            if (exitCode != null && exitCode == 0 && nativeFailure == null) {
                finishWithStatus(Activity.RESULT_OK, "Native backend probe finished with exit code 0");
            } else {
                finishWithStatus(Activity.RESULT_CANCELED, nativeFailure);
            }
        } catch (Exception error) {
            finishWithStatus(Activity.RESULT_CANCELED, "Probe receipt failed: " + error);
        }
    }

    private void finishWithStatus(int code, String message) {
        Bundle status = new Bundle();
        status.putString("probeStatus", message);
        finish(code, status);
    }

    private static void writeReceipt(Path path, JSONObject receipt) throws IOException {
        Files.write(path, (receipt.toString() + "\n").getBytes(StandardCharsets.UTF_8),
                StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE);
    }

    private static native int runNative(String runDir, String backend, String testCase);
}
